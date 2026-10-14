"""Cancellable HTTP reads in one session-owned process; no ledger/cache writes.

The public HTTP facade validates upstream destinations before this transport.
Only the parent reserves/charges data. Terminating this worker closes active
network handles even when connect, DNS, headers, or a body read is blocked.
"""

import multiprocessing
import time
import urllib.error
import urllib.request

from .errors import DataError, DeadlineError
from .storage import HEADER_RESERVE

CHUNK = 65_536
CLEANUP_TOLERANCE_SECONDS = 2


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        error = DataError("redirect_rejected", "Public-source redirect rejected")
        error.received_header_bytes = len(headers.as_bytes()) + 128
        error.received_unknown = True
        raise error


def _worker(connection):
    opener = urllib.request.build_opener(NoRedirect())
    try:
        while True:
            request_data = connection.recv()
            url, body, headers, ceiling, deadline = request_data
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeadlineError()
                request = urllib.request.Request(url, body, headers)
                with opener.open(request, timeout=min(20, remaining)) as response:
                    count = len(response.headers.as_bytes()) + 128
                    connection.send(("count", count))
                    if count > HEADER_RESERVE:
                        raise DataError("response_too_large", "Unexpectedly large response headers")
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > ceiling:
                        raise DataError("response_too_large", "Response exceeds bounded read size")
                    size = 0
                    reader = getattr(response, "read1", response.read)
                    while size < ceiling:
                        if time.monotonic() >= deadline:
                            raise DeadlineError()
                        chunk = reader(min(CHUNK, ceiling - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        connection.send(("chunk", chunk))
                    if (declared and size != int(declared)) or (declared is None and size == ceiling):
                        raise DataError("response_incomplete", "Incomplete or bounded response")
                if time.monotonic() >= deadline:
                    raise DeadlineError()
                connection.send(("done",))
            except urllib.error.HTTPError as exc:
                try:
                    connection.send(("count", len(exc.headers.as_bytes()) + 128))
                    # The parent deadline also interrupts a blocked error-body read.
                    connection.send(("count", len(exc.read(4096))))
                    connection.send(("error", "source_unavailable", f"HTTP {exc.code}", False))
                except (TimeoutError, OSError, urllib.error.URLError) as error:
                    expired = time.monotonic() >= deadline
                    connection.send(("error", "time_budget_exhausted" if expired else "source_unavailable",
                                     "Overall lookup deadline expired" if expired else str(error), True))
                finally:
                    exc.close()
            except DeadlineError as exc:
                connection.send(("error", exc.code, str(exc), True))
            except DataError as exc:
                extra_headers = getattr(exc, "received_header_bytes", 0)
                if extra_headers:
                    connection.send(("count", extra_headers))
                connection.send(("error", exc.code, str(exc), bool(getattr(exc, "received_unknown", False))))
            except (TimeoutError, OSError, urllib.error.URLError) as exc:
                expired = time.monotonic() >= deadline
                connection.send(("error", "time_budget_exhausted" if expired else "source_unavailable",
                                 "Overall lookup deadline expired" if expired else str(exc), True))
            except (ValueError, TypeError) as exc:
                connection.send(("error", "invalid_response", str(exc), False))
            except Exception as exc:
                connection.send(("error", "source_unavailable", f"HTTP worker failure: {exc}", True))
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
    finally:
        connection.close()


class ProcessHTTP:
    """One persistent read worker; startup time consumes the caller's deadline."""

    def __init__(self):
        self.process = None
        self.connection = None

    def _start(self):
        if self.process is not None:
            return
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker, args=(child,), daemon=True, name="hemi-public-read")
        try:
            process.start()
        except Exception:
            parent.close()
            child.close()
            raise
        child.close()
        self.process, self.connection = process, parent

    def read(self, url, body, headers, ceiling, deadline, on_count):
        if time.monotonic() >= deadline:
            raise DeadlineError()
        self._start()
        result = bytearray()
        try:
            if time.monotonic() >= deadline:
                raise DeadlineError()
            self.connection.send((url, body, headers, ceiling, deadline))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeadlineError()
                if not self.connection.poll(min(remaining, 0.05)):
                    if not self.process.is_alive():
                        raise EOFError("HTTP worker exited before completing its response")
                    continue
                message = self.connection.recv()
                if time.monotonic() >= deadline:
                    raise DeadlineError()
                if message[0] == "count":
                    on_count(message[1])
                elif message[0] == "chunk":
                    if len(result) + len(message[1]) > ceiling:
                        raise DataError("response_too_large", "Worker exceeded the response bound")
                    result.extend(message[1])
                    on_count(len(message[1]))
                elif message[0] == "done":
                    return bytes(result)
                elif message[0] == "error":
                    _, code, text, uncertain = message
                    if code == "time_budget_exhausted":
                        raise DeadlineError(text)
                    error = DataError(code, text)
                    error.received_unknown = uncertain
                    raise error
        except DeadlineError:
            self.close()  # Kill blocked reads before returning/releasing session.
            raise
        except (EOFError, OSError) as exc:
            self.close()
            error = DataError("source_unavailable", f"HTTP worker interrupted: {exc}")
            error.received_unknown = True
            raise error from exc

    def close(self):
        process, connection = self.process, self.connection
        self.process, self.connection = None, None
        if connection is not None:
            connection.close()
        if process is not None:
            process.join(0.1)
            if process.is_alive():
                process.terminate()
                process.join(0.5)
            if process.is_alive():
                process.kill()
                process.join(0.5)
            if process.is_alive():
                raise DataError("transport_cleanup_failed", "HTTP worker did not exit within cleanup tolerance")
            process.close()
