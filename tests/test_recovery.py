"""Controlled outage/metadata recovery tests; no live reorg or public reads."""

import copy
import json
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from hemi_lookup.bitcoin import sha256d, display_hash, header_pow
from hemi_lookup.engine import Engine
from hemi_lookup.errors import DataError, BudgetError, DeadlineError
from hemi_lookup.network import PublicSources, HEMI_RPCS, BTC_APIS
from hemi_lookup.proof import verify_receipt
from hemi_lookup.recovery import HemiRecovery
from hemi_lookup.storage import Session
from hemi_lookup.timestamps import TimestampRecovery, GENESIS
from hash_fixtures import HashSources, REFERENCE, ORDINARY, FixtureCache


class RecoveryRPCFixture:
    def __init__(self, mode="baseline", fail_method="eth_getTransactionByBlockNumberAndIndex"):
        self.primary = HashSources(ORDINARY)
        self.alternate = HashSources(ORDINARY, "canonical_changed" if mode == "conflict" else "wrong_network" if mode == "wrong_network" else "baseline")
        self.mode, self.fail_method, self.calls = mode, fail_method, []
    def read(self, url, ceiling=262144, rpc=None, block=False):
        self.calls.append((url, rpc["method"] if rpc else None))
        if rpc:
            if url == HEMI_RPCS[0] and rpc["method"] == self.fail_method:
                raise DataError("source_unavailable", "Controlled primary RPC outage")
            if url == HEMI_RPCS[1] and self.mode == "both_unavailable":
                raise DataError("source_unavailable", "Controlled alternate outage")
            source = self.primary if url == HEMI_RPCS[0] else self.alternate
            value = source.rpc(rpc["method"], rpc["params"])
            return json.dumps({"jsonrpc":"2.0","id":1,"result":value}).encode()
        api = next(api for api in BTC_APIS if url.startswith(api))
        path = url[len(api):]
        value = self.primary.btc(path, as_json=not (path.startswith("/block-height/") or path == "/blocks/tip/hash"))
        return (value if isinstance(value,str) else json.dumps(value)).encode()


class HemiRecoveryTests(unittest.TestCase):
    def operation(self, mode="baseline", refresh=False):
        with tempfile.TemporaryDirectory(prefix="hemi_recovery_test_") as directory:
            with Session(directory) as session:
                sources = PublicSources(session)
                fixture = RecoveryRPCFixture(mode)
                sources.http.read = fixture.read
                engine = Engine(sources,session,FixtureCache())
                result = engine.refresh(copy.deepcopy(ORDINARY)) if refresh else engine.lookup(ORDINARY["query"]["block_hash"], (965931,965931))
                return result, fixture

    def test_mid_lookup_outage_replays_data_and_preserves_exact_hash(self):
        result, fixture = self.operation()
        self.assertEqual(result["status"],"found")
        self.assertEqual(result["query"]["requested_block_hash"],ORDINARY["query"]["block_hash"])
        self.assertTrue(result["source_recovery"]["hemi_fallback_used"])
        self.assertEqual(result["sources"]["hemi_rpc_used"],HEMI_RPCS[1])
        activated=next(e for e in result["source_recovery"]["hemi_events"] if e["kind"]=="hemi_fallback_activated")
        self.assertGreater(activated["revalidated_observations"],1)
        self.assertEqual(verify_receipt(result)["proofs_checked"],4)

    def test_alternate_conflict_does_not_substitute_target(self):
        result,_=self.operation("conflict")
        self.assertEqual(result["status"],"incomplete")
        self.assertEqual(result["source_recovery"]["hemi_failure"]["code"],"source_conflict")
        self.assertFalse(result["source_recovery"]["hemi_fallback_used"])
        self.assertEqual(result["query"]["requested_block_hash"],ORDINARY["query"]["block_hash"])

    def test_alternate_wrong_network_is_rejected(self):
        result,_=self.operation("wrong_network")
        self.assertEqual(result["status"],"incomplete")
        self.assertEqual(result["source_recovery"]["hemi_failure"]["code"],"wrong_network")
        self.assertFalse(result["context"]["current_view_confirmed"])

    def test_two_unavailable_sources_do_not_repeat_recovery(self):
        result,fixture=self.operation("both_unavailable")
        self.assertEqual(result["status"],"incomplete")
        self.assertEqual(sum(url==HEMI_RPCS[1] for url,m in fixture.calls),1)
        self.assertEqual(result["source_recovery"]["hemi_failure"]["code"],"source_unavailable")

    def test_refresh_recovery_preserves_original_proof_query_and_coverage(self):
        # Refresh uses header reads, so force an outage on its first network call.
        with tempfile.TemporaryDirectory(prefix="hemi_refresh_recovery_") as directory:
            with Session(directory) as session:
                sources=PublicSources(session); fixture=RecoveryRPCFixture(fail_method="eth_chainId")
                sources.http.read=fixture.read
                original=copy.deepcopy(ORDINARY)
                result=Engine(sources,session,None).refresh(original)
                self.assertEqual(result["status"],"found")
                self.assertEqual(result["query"],original["query"])
                self.assertEqual(result["coverage"],original["coverage"])
                self.assertEqual(verify_receipt(result)["proofs_checked"],4)
                self.assertTrue(result["source_recovery"]["hemi_fallback_used"])

    def test_conflicted_refresh_retains_mathematical_proof(self):
        with tempfile.TemporaryDirectory(prefix="hemi_refresh_conflict_") as directory:
            with Session(directory) as session:
                fixture=RecoveryRPCFixture("conflict",fail_method="eth_getBlockByNumber")
                # Observe the requested header first; fail at a later checkpoint.
                original_read=fixture.read
                def reader(url, ceiling=262144, rpc=None, block=False):
                    if rpc and url==HEMI_RPCS[0] and rpc["method"]=="eth_getBlockByNumber" and int(rpc["params"][0],16)==ORDINARY["query"]["block_number"]:
                        return json.dumps({"result":fixture.primary.rpc(rpc["method"],rpc["params"])}).encode()
                    return original_read(url,ceiling,rpc,block)
                sources=PublicSources(session); sources.http.read=reader
                result=Engine(sources,session,None).refresh(ORDINARY)
                self.assertEqual(result["status"],"incomplete")
                self.assertIsNone(result["selection"]["current_proof_index"])
                self.assertEqual(verify_receipt(result)["proofs_checked"],4)
                self.assertEqual(result["source_recovery"]["hemi_failure"]["code"],"source_conflict")

    def test_deadline_and_byte_budget_never_start_alternate(self):
        for error in (DeadlineError(),BudgetError("controlled data ceiling")):
            calls=[]
            def fetch(endpoint,method,params): calls.append(endpoint); raise error
            recovery=HemiRecovery(HEMI_RPCS,fetch,lambda:None)
            with self.subTest(error=type(error).__name__),self.assertRaises(BudgetError): recovery.rpc("eth_chainId",[])
            self.assertEqual(calls,[HEMI_RPCS[0]])

    def test_deadline_during_replay_does_not_activate_backup(self):
        expired=False; calls=[]
        def check():
            if expired: raise DeadlineError()
        def fetch(endpoint,method,params):
            nonlocal expired
            calls.append((endpoint,method))
            if endpoint==HEMI_RPCS[0] and method=="eth_getBlockByHash": raise DataError("source_unavailable","outage")
            if endpoint==HEMI_RPCS[1]: expired=True
            return "0xa867"
        recovery=HemiRecovery(HEMI_RPCS,fetch,check)
        recovery.rpc("eth_chainId",[])
        with self.assertRaises(DeadlineError): recovery.rpc("eth_getBlockByHash",["0x"+"11"*32,False])
        self.assertFalse(recovery.switched)

    def test_optional_client_version_failure_does_not_force_recovery(self):
        calls=[]
        def fetch(endpoint,method,params): calls.append(endpoint); raise DataError("source_unavailable","optional method unavailable")
        recovery=HemiRecovery(HEMI_RPCS,fetch,lambda:None)
        with self.assertRaises(DataError): recovery.rpc("web3_clientVersion",[])
        self.assertEqual(calls,[HEMI_RPCS[0]])
        self.assertFalse(recovery.attempted)

    def test_failure_after_activation_stays_explicit_and_is_not_retried(self):
        calls=[]
        def fetch(endpoint,method,params):
            calls.append((endpoint,method))
            if endpoint==HEMI_RPCS[1] and method=='eth_chainId': return '0xa867'
            raise DataError('source_unavailable','controlled unavailable metadata')
        recovery=HemiRecovery(HEMI_RPCS,fetch,lambda:None)
        recovery.rpc('eth_chainId',[])
        with self.assertRaises(DataError): recovery.rpc('eth_getBlockByNumber',['0x19',False])
        previous=len(calls)
        with self.assertRaises(DataError): recovery.rpc('eth_chainId',[])
        self.assertEqual(len(calls),previous)
        self.assertEqual(recovery.snapshot()['hemi_failure']['code'],'source_unavailable')

    def test_same_source_change_withholds_current_selection_and_retains_proof(self):
        with tempfile.TemporaryDirectory(prefix="hemi_changed_view_") as directory:
            with Session(directory) as session:
                fixture=RecoveryRPCFixture(fail_method="never")
                fixture.primary.mode="changed_after_scan"
                sources=PublicSources(session); sources.http.read=fixture.read
                result=Engine(sources,session,FixtureCache()).lookup(ORDINARY["query"]["block_hash"],(965931,965931))
                self.assertEqual(result["status"],"incomplete")
                self.assertEqual(result["source_recovery"]["hemi_failure"]["code"],"source_conflict")
                self.assertIsNone(result["selection"]["current_proof_index"])
                self.assertEqual(verify_receipt(result)["proofs_checked"],4)


class SyntheticBitcoin:
    """Unmined linked headers for metadata logic; the test explicitly stubs PoW."""
    genesis_header=bytes.fromhex("01000000"+"00"*32+"3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a"+"29ab5f49ffff001d1dac2b7c")
    def __init__(self,tip=100,mode="baseline"):
        self.tip,self.mode,self.calls=tip,mode,[]
        self.headers={0:self.genesis_header}; self.meta={}
        self.base=struct.unpack_from("<I",self.genesis_header,68)[0]
        for h in range(tip+1):
            stamp=self.base+{52:505,53:490}.get(h,h*10)
            if h:
                self.headers[h]=struct.pack("<I",1)+sha256d(self.headers[h-1])+b"\x11"*32+struct.pack("<III",stamp,0x1d00ffff,0)
            times=sorted(struct.unpack_from("<I",self.headers[n],68)[0] for n in range(max(0,h-10),h+1))
            self.meta[h]={"height":h,"id":display_hash(sha256d(self.headers[h])),"timestamp":stamp,"mediantime":times[len(times)//2]}
        self.by_hash={m["id"]:h for h,m in self.meta.items()}
    def fetch(self,path,as_json=True):
        self.calls.append(path)
        if path=="/blocks/tip/hash": return self.meta[self.tip]["id"]
        if path.startswith("/block-height/"): return self.meta[int(path.rsplit('/',1)[1])]["id"]
        if path.startswith("/blocks/"):
            end=int(path.rsplit('/',1)[1]); rows=[copy.deepcopy(self.meta[n]) for n in range(end,max(-1,end-10),-1)]
            return rows[1:] if self.mode=="gap" else rows
        digest=path.split('/')[2]; height=self.by_hash[digest]
        if path.endswith('/header'):
            return "00"*80 if self.mode=="bad_header" else self.headers[height].hex()
        return copy.deepcopy(self.meta[height])


class TimestampRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.synthetic_pow=patch("hemi_lookup.timestamps.header_pow",return_value=True)
        self.synthetic_pow.start()
        self.addCleanup(self.synthetic_pow.stop)
    def test_nonmonotonic_raw_time_uses_mtp_and_local_neighborhood(self):
        fixture=SyntheticBitcoin(); result=TimestampRecovery(fixture.fetch,lambda:None).hint(fixture.base+510)
        self.assertEqual(result["lookup"]["height"],53)
        self.assertFalse(result["global_timestamp_floor_proven"])
        self.assertEqual(len(result["recovery"]["mtp_header_control"]["supplied_headers"]),11)

    def test_future_timestamp_clamps_to_observed_tip(self):
        fixture=SyntheticBitcoin(); result=TimestampRecovery(fixture.fetch,lambda:None).hint(fixture.base+2000)
        self.assertEqual(result["lookup"]["height"],100)
        self.assertEqual(result["recovery"]["boundary_mode"],"clamped_to_observed_tip")

    def test_recent_timestamp_can_select_before_tip(self):
        fixture=SyntheticBitcoin(); result=TimestampRecovery(fixture.fetch,lambda:None).hint(fixture.base+995)
        self.assertEqual(result["lookup"]["height"],99)

    def test_before_genesis_and_genesis_only_chain(self):
        fixture=SyntheticBitcoin(0)
        result=TimestampRecovery(fixture.fetch,lambda:None).hint(fixture.base-1)
        self.assertEqual(result["lookup"]["height"],0)
        self.assertEqual(result["recovery"]["boundary_mode"],"clamped_before_genesis")
        result=TimestampRecovery(fixture.fetch,lambda:None).hint(fixture.base+10)
        self.assertEqual(result["lookup"]["height"],0)

    def test_gaps_bad_headers_and_changed_anchor_stay_incomplete(self):
        for mode in ("gap","bad_header","changed_anchor"):
            fixture=SyntheticBitcoin(mode=mode)
            def fetch(path,as_json=True):
                if mode=="changed_anchor" and path=="/block-height/100": return "22"*32
                return fixture.fetch(path,as_json)
            with self.subTest(mode=mode),self.assertRaises(DataError): TimestampRecovery(fetch,lambda:None).hint(fixture.base+510)

    def test_deadline_stops_metadata_before_any_request(self):
        fixture=SyntheticBitcoin()
        def check(): raise DeadlineError()
        with self.assertRaises(DeadlineError): TimestampRecovery(fixture.fetch,check).hint(fixture.base+510)
        self.assertEqual(fixture.calls,[])

    def test_primary_timestamp_failure_pins_recovery_without_retrying_mempool(self):
        fixture=SyntheticBitcoin()
        with tempfile.TemporaryDirectory(prefix="hemi_timestamp_route_") as directory:
            with Session(directory) as session:
                sources=PublicSources(session); calls=[]
                def reader(url,ceiling=262144,rpc=None,block=False):
                    calls.append(url)
                    if url.startswith(BTC_APIS[1]): raise DataError("source_unavailable","controlled mempool outage")
                    path=url[len(BTC_APIS[0]):]
                    value=fixture.fetch(path,not (path.startswith('/block-height/') or path.endswith('/header') or path=='/blocks/tip/hash'))
                    return (value if isinstance(value,str) else json.dumps(value)).encode()
                sources.http.read=reader
                sources.timestamp(fixture.base+510); sources.timestamp(fixture.base+600)
                self.assertEqual(sum(url.startswith(BTC_APIS[1]) for url in calls),1)
                self.assertTrue(sources.provenance()["timestamp_recovery_used"])

    def test_primary_timestamp_conflict_is_not_hidden_by_recovery(self):
        with tempfile.TemporaryDirectory(prefix="hemi_timestamp_conflict_") as directory:
            with Session(directory) as session:
                sources=PublicSources(session)
                def reader(url,ceiling=262144,rpc=None,block=False):
                    if '/timestamp/' in url: return json.dumps({'height':10,'hash':'11'*32}).encode()
                    if '/block-height/' in url: return ('22'*32).encode()
                    return json.dumps({'id':'11'*32,'height':10}).encode()
                sources.http.read=reader
                with self.assertRaises(DataError): sources.timestamp(100)
                self.assertFalse(sources.timestamp_failed)
                self.assertIsNone(sources.timestamp_recovery)
                self.assertEqual(sources.provenance()["bitcoin_failure"]["code"],"source_conflict")

    def test_primary_timestamp_deadline_never_starts_recovery(self):
        with tempfile.TemporaryDirectory(prefix="hemi_timestamp_deadline_") as directory:
            with Session(directory) as session:
                sources=PublicSources(session); calls=[]
                def reader(*args,**kwargs): calls.append(args[0]); raise DeadlineError()
                sources.http.read=reader
                with self.assertRaises(DeadlineError): sources.timestamp(100)
                self.assertEqual(len(calls),1)
                self.assertFalse(sources.timestamp_failed)


class RetainedHeaderControl(unittest.TestCase):
    def test_actual_mainnet_median_headers_verify_without_pow_stub(self):
        from pathlib import Path
        root=Path(__file__).resolve().parent.parent
        evidence=json.loads((root/'tests/fixtures/source-feasibility/bitcoin-metadata.json').read_text())
        control=evidence['results'][0]['cases'][0]['mtp_header_control']
        by_height={h['height']:h for h in control['headers']}
        by_hash={h['hash']:h for h in control['headers']}
        def fetch(path,as_json=True):
            if path.startswith('/block-height/'): return by_height[int(path.rsplit('/',1)[1])]['hash']
            h=by_hash[path.split('/')[2]]
            if path.endswith('/header'): return h['header_hex']
            return {'id':h['hash'],'height':h['height'],'timestamp':h['timestamp'],
                    'mediantime':control['mediantime_recomputed'] if h['height']==control['height'] else 0}
        result=TimestampRecovery(fetch,lambda:None).median_control(control['height'])
        self.assertEqual(result['mediantime'],control['mediantime_recomputed'])
        self.assertTrue(all(header_pow(bytes.fromhex(h['header_hex'])) for h in result['supplied_headers']))


if __name__=="__main__": unittest.main()
