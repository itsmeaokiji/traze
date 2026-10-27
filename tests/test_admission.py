"""Loopback overload replies, bounded connection cleanup and capacity recovery."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from hemi_lookup.web import Jobs, LocalServer


class AdmissionTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = LocalServer(0, Jobs(self.root))
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .005}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.held = 0
        for target in ('hemi_lookup.web.PublicSources', 'hemi_lookup.web.Session'):
            guard = patch(target, side_effect=AssertionError('No source operation'))
            guard.start()
            self.addCleanup(guard.stop)
        connect = socket.socket.connect
        def local_only(sock, address):
            if address[0] != '127.0.0.1':
                raise AssertionError('Only loopback transport is allowed')
            return connect(sock, address)
        guard = patch.object(socket.socket, 'connect', local_only)
        guard.start()
        self.addCleanup(guard.stop)

    def stop_server(self):
        self.release_slots()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertFalse((self.root / '.hemi_lookup_data/session.json').exists())

    def fill_slots(self):
        for _ in range(16):
            self.assertTrue(self.server.connections.acquire(blocking=False))
            self.held += 1
        self.assertFalse(self.server.connections.acquire(blocking=False))

    def release_slots(self):
        while self.held:
            self.server.connections.release()
            self.held -= 1

    def request(self, method='GET', path='/api/v1/index', body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body=body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def assert_recovered(self):
        self.release_slots()
        self.assertEqual(self.request(path='/favicon.ico')[0], 204)

    def test_saturated_requests_receive_retryable_json(self):
        self.fill_slots()
        status, headers, raw = self.request()
        self.assertEqual(status, 503)
        self.assertEqual(headers['Retry-After'], '1')
        self.assertEqual(headers['Connection'], 'close')
        self.assertEqual(int(headers['Content-Length']), len(raw))
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
        self.assertEqual(json.loads(raw)['error']['code'], 'read_busy')
        self.assert_recovered()

    def test_split_request_can_finish_after_receiving_busy_reply(self):
        self.fill_slots()
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as client:
            client.sendall(b'GET /api/v1/index HTTP/1.1\r\nHost: localhost\r\n')
            raw = client.recv(4096)
            self.assertTrue(raw.startswith(b'HTTP/1.0 503'))
            client.sendall(b'Connection: close\r\n\r\n')
            client.shutdown(socket.SHUT_WR)
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                raw += chunk
            headers, body = raw.split(b'\r\n\r\n', 1)
            self.assertIn(b'Retry-After: 1', headers)
            self.assertEqual(json.loads(body)['error']['code'], 'read_busy')
        self.assert_recovered()

    def test_rejected_post_does_not_dispatch_or_start_a_job(self):
        self.fill_slots()
        with patch.object(self.server, 'process_request_thread', side_effect=AssertionError('No worker')):
            status, _, raw = self.request('POST', '/api/lookup', b'{"block": 5243119}')
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(raw)['error']['code'], 'read_busy')
        self.assertIsNone(self.server.jobs.active)
        self.assertEqual(self.server.jobs.jobs, {})
        self.assertFalse((self.root / '.hemi_lookup_data/receipts').exists())
        self.assert_recovered()

    def test_unfinished_peer_cannot_hold_the_accept_loop(self):
        self.fill_slots()
        closed = threading.Event()
        original = self.server.shutdown_request
        def shutdown(request):
            try:
                original(request)
            finally:
                closed.set()
        with patch.object(self.server, 'shutdown_request', side_effect=shutdown):
            with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as client:
                client.sendall(b'GET /unfinished')
                self.assertTrue(client.recv(4096).startswith(b'HTTP/1.0 503'))
                self.assertTrue(closed.wait(1), 'The unfinished peer held admission cleanup')
                self.assert_recovered()

    def test_closed_peer_is_contained_and_slots_recover(self):
        self.fill_slots()
        closed = threading.Event()
        original = self.server.shutdown_request
        def shutdown(request):
            try:
                original(request)
            finally:
                closed.set()
        with patch.object(self.server, 'shutdown_request', side_effect=shutdown):
            with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2):
                pass
            self.assertTrue(closed.wait(1))
        self.assert_recovered()


class AdmissionBoundaryTests(unittest.TestCase):
    def server(self):
        server = LocalServer.__new__(LocalServer)
        server.connections = threading.BoundedSemaphore(16)
        for _ in range(16):
            self.assertTrue(server.connections.acquire(blocking=False))
        server.shutdown_request = Mock()
        return server

    def test_incoming_data_is_discarded_with_a_fixed_byte_cap(self):
        server = self.server()
        peer = Mock()
        peer.recv.side_effect = lambda count: b'x' * count
        with patch('hemi_lookup.web.time.monotonic', return_value=0):
            server.process_request(peer, ('127.0.0.1', 1))
        self.assertEqual(sum(call.args[0] for call in peer.recv.call_args_list), 65536)
        self.assertFalse(server.connections.acquire(blocking=False))
        peer.shutdown.assert_called_once_with(socket.SHUT_WR)
        server.shutdown_request.assert_called_once_with(peer)

    def test_one_deadline_covers_send_and_every_incoming_chunk(self):
        server = self.server()
        peer = Mock()
        peer.recv.return_value = b'x'
        with patch('hemi_lookup.web.time.monotonic', side_effect=[0, .04, .08, .12]):
            server.process_request(peer, ('127.0.0.1', 1))
        self.assertEqual(peer.recv.call_count, 2)
        self.assertAlmostEqual(peer.settimeout.call_args_list[0].args[0], .1)
        self.assertAlmostEqual(peer.settimeout.call_args_list[1].args[0], .06)
        self.assertAlmostEqual(peer.settimeout.call_args_list[2].args[0], .02)
        server.shutdown_request.assert_called_once_with(peer)

    def test_peer_failures_still_close_without_dispatch_or_slot_release(self):
        for phase in ('sendall', 'shutdown', 'recv'):
            with self.subTest(phase=phase):
                server = self.server()
                peer = Mock()
                getattr(peer, phase).side_effect = ConnectionResetError('Controlled peer close')
                server.process_request(peer, ('127.0.0.1', 1))
                server.shutdown_request.assert_called_once_with(peer)
                self.assertFalse(server.connections.acquire(blocking=False))

    def test_dispatch_failure_releases_the_one_admitted_slot(self):
        server = self.server()
        server.connections.release()
        with patch('http.server.ThreadingHTTPServer.process_request', side_effect=RuntimeError('Controlled dispatch failure')):
            with self.assertRaisesRegex(RuntimeError, 'Controlled dispatch failure'):
                server.process_request(Mock(), ('127.0.0.1', 1))
        self.assertTrue(server.connections.acquire(blocking=False))
        self.assertFalse(server.connections.acquire(blocking=False))
        server.shutdown_request.assert_not_called()

    def test_worker_failure_releases_its_admitted_slot(self):
        server = self.server()
        with patch('http.server.ThreadingHTTPServer.process_request_thread', side_effect=RuntimeError('Controlled worker failure')):
            with self.assertRaisesRegex(RuntimeError, 'Controlled worker failure'):
                server.process_request_thread(Mock(), ('127.0.0.1', 1))
        self.assertTrue(server.connections.acquire(blocking=False))
        self.assertFalse(server.connections.acquire(blocking=False))


if __name__ == '__main__':
    unittest.main()
