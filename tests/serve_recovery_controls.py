"""Actual browser/engine with controlled provider recovery; no public reads."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from hash_fixtures import ORDINARY,REFERENCE,BODY
from test_recovery import RecoveryRPCFixture
from hemi_lookup.network import PublicSources,HEMI_RPCS
from hemi_lookup.engine import Engine
from hemi_lookup.storage import BlockCache
from hemi_lookup.web import Jobs,LocalServer


class RecoveryFactory:
    def __init__(self): self.operations=0
    def __call__(self,session):
        self.operations+=1
        sources=PublicSources(session)
        fixture=RecoveryRPCFixture("conflict" if self.operations==2 else "baseline",
                                   fail_method="eth_getBlockByNumber" if self.operations==2 else "eth_getTransactionByBlockNumberAndIndex")
        ordinary_read=fixture.read
        def read(url,ceiling=262144,rpc=None,block=False):
            if (self.operations==2 and rpc and url==HEMI_RPCS[0] and rpc['method']=='eth_getBlockByNumber'
                    and int(rpc['params'][0],16)==ORDINARY['query']['block_number']):
                return json.dumps({'result':fixture.primary.rpc(rpc['method'],rpc['params'])}).encode()
            return ordinary_read(url,ceiling,rpc,block)
        sources.http.read=read
        return sources


class BrowserEngine(Engine):
    def lookup(self,*args,**kwargs):
        r=super().lookup(*args,**kwargs)
        r['validation']={'scope':'controlled_provider_responses','live_public_reads':False}
        return r


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='hemi_recovery_ui_qa_') as directory:
        root=Path(directory).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        (root/'demo_results').mkdir()
        (root/'demo_results/mainnet_5243117.json').write_text(json.dumps(ORDINARY),encoding='utf-8')
        jobs=Jobs(root, mode="discovery")
        BlockCache(jobs.state_dir/'blocks').put(REFERENCE['proofs'][0]['bitcoin_block_hash'],BODY)
        with patch('hemi_lookup.web.PublicSources',RecoveryFactory()),patch('hemi_lookup.web.Engine',BrowserEngine):
            server=LocalServer(8767,jobs)
            print(json.dumps({'url':'http://127.0.0.1:8767','scope':'controlled provider replies; first lookup recovers, next refresh conflicts',
                              'hash':ORDINARY['query']['block_hash']}),flush=True)
            try: server.serve_forever(poll_interval=.1)
            except KeyboardInterrupt: pass
            finally: server.server_close()
