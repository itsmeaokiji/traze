"""Fixed public read-only endpoints, with every response charged to the session."""

import json
import urllib.error
import urllib.parse
import urllib.request

from .errors import CollectionStopped, DataError, BudgetError, DeadlineError
from .storage import HEADER_RESERVE
from .transport import ProcessHTTP, NoRedirect
from .recovery import HemiRecovery
from .bitcoin import hex32
from .storage import now

HEMI_RPC = "https://rpc.hemi.network/rpc"
HEMI_RPCS = (HEMI_RPC, "https://hemi.drpc.org")
BTC_APIS = ("https://blockstream.info/api", "https://mempool.space/api")
HOSTS = {"rpc.hemi.network", "hemi.drpc.org", "blockstream.info", "mempool.space"}
METHODS = {"eth_chainId", "web3_clientVersion", "eth_getBlockByNumber", "eth_getBlockByHash", "eth_getTransactionByBlockNumberAndIndex",
           "eth_getProof", "eth_getTransactionCount"}


class HTTP:
    def __init__(self, session):
        self.session = session
        self.opener = urllib.request.build_opener(NoRedirect())
        self._default_opener = self.opener
        self.transport = ProcessHTTP()
        self.session.on_close(self.close)

    def close(self):
        self.transport.close()

    def read(self, url, ceiling=262144, rpc=None, block=False):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in (None, 443) or parsed.username:
            raise DataError("destination_rejected", "Only configured public HTTPS sources are allowed")
        if rpc is not None and (url not in HEMI_RPCS or rpc.get("method") not in METHODS):
            raise DataError("method_rejected", "Only basic read-only Hemi RPC methods are allowed")
        entry = self.session.reserve(url, ceiling, block)
        if rpc is not None:
            entry["rpc_method"] = rpc["method"]
            self.session.save()
        headers = {"User-Agent": "hemi-public-data-readonly-demo", "Accept-Encoding": "identity"}
        body = None
        if rpc is not None:
            body = json.dumps(rpc).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, body, headers)
        count, result, status, error, uncertain = 0, bytearray(), "error", None, False

        def count_received(amount):
            nonlocal count
            count += amount

        try:
            if self.opener is self._default_opener:
                raw = self.transport.read(url, body, headers, ceiling, self.session.deadline, count_received,
                                          check=self.session.check_deadline)
                self.session.check_deadline()
                status = "ok"
                return raw
            # Trusted, non-network test transports retain the existing opener
            # hook. The actual public client always uses the cancellable worker.
            with self.opener.open(request, timeout=min(20, self.session.remaining_seconds())) as response:
                self.session.check_deadline()
                count = len(response.headers.as_bytes()) + 128
                if count > HEADER_RESERVE:
                    raise DataError("response_too_large", "Unexpectedly large response headers")
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > ceiling:
                    raise DataError("response_too_large", "Response exceeds bounded read size")
                while len(result) < ceiling:
                    self.session.check_deadline()
                    chunk = response.read(min(65536, ceiling - len(result)))
                    if not chunk:
                        break
                    result.extend(chunk)
                    count += len(chunk)
                    self.session.check_deadline()
                if (declared and len(result) != int(declared)) or (declared is None and len(result) == ceiling):
                    raise DataError("response_incomplete", "Incomplete or bounded response")
            self.session.check_deadline()
            status = "ok"
            return bytes(result)
        except (CollectionStopped, KeyboardInterrupt) as exc:
            uncertain, status, error = True, "operator_interrupted", str(exc) or "Operator interrupted collection"
            self.transport.close()
            raise
        except DeadlineError as exc:
            self.session.deadline_expired = True
            uncertain, status, error = True, "deadline_interrupted", str(exc)
            self.transport.close()
            raise
        except urllib.error.HTTPError as exc:
            count += len(exc.headers.as_bytes()) + len(exc.read(4096)) + 128
            error = f"HTTP {exc.code}"
            raise DataError("source_unavailable", error) from exc
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            error = str(exc)
            raise DataError("source_unavailable", error) from exc
        except Exception as exc:
            uncertain = bool(getattr(exc, "received_unknown", False))
            error = str(exc)
            raise
        finally:
            self.session.charge(entry, count, status, error, uncertain)


class PublicSources:
    def __init__(self, session):
        self.http = HTTP(session)
        self.last_btc_api = None
        self.hemi = HemiRecovery(HEMI_RPCS, self._rpc_at, session.check_deadline)
        self.timestamp_recovery = None
        self.timestamp_events = []
        self.timestamp_failed = False
        self.timestamp_seeds = {}
        self.bitcoin_conflict = None
        self.last_evidence_rpc = None
        self.progress = lambda message: None

    def set_progress(self, callback):
        self.progress = callback

    def _rpc_at(self, endpoint, method, params):
        try:
            ceiling = 2_000_000 if method in ("eth_getBlockByNumber", "eth_getBlockByHash") and len(params) == 2 and params[1] is True else 524288
            data = json.loads(self.http.read(endpoint, ceiling,
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
        except (ValueError, TypeError) as exc:
            raise DataError("invalid_response", "Hemi RPC response is not valid JSON") from exc
        self.http.session.check_deadline()
        if not isinstance(data, dict):
            raise DataError("invalid_response", "Hemi RPC response is not an object")
        if data.get("error") or data.get("result") is None:
            raise DataError("source_unavailable", f"{method}: {data.get('error', 'null result')}")
        return data["result"]

    def rpc(self, method, params):
        if method in ("eth_getProof", "eth_getTransactionCount"):
            # Optional archive availability is separate from a contradictory
            # chain observation. Proofs still authenticate against exact roots;
            # account nonces only nominate an anchor for full authentication.
            errors = []
            endpoints = (self.hemi.active,) + tuple(url for url in HEMI_RPCS if url != self.hemi.active)
            for endpoint in endpoints:
                self.http.session.check_deadline()
                try:
                    value = self._rpc_at(endpoint, method, params)
                    self.last_evidence_rpc = endpoint
                    return value
                except BudgetError:
                    raise
                except DataError as exc:
                    errors.append(str(exc))
            raise DataError("history_source_unavailable", "The public RPCs could not return the required historical evidence.")
        return self.hemi.rpc(method, params)

    def provenance(self):
        return {**self.hemi.snapshot(), "timestamp_recovery_used": self.timestamp_failed,
                "timestamp_events": list(self.timestamp_events), "bitcoin_failure": self.bitcoin_conflict}

    def conflict(self, message):
        self.bitcoin_conflict = {"code": "source_conflict", "message": message}
        raise DataError("source_conflict", message)

    def pinned_bitcoin(self, path, as_json=True):
        # Recovery stays on Blockstream; it never returns to a failing mempool
        # service behind the caller's back.
        try:
            raw = self.http.read(BTC_APIS[0] + path)
            value = json.loads(raw) if as_json else raw.decode().strip()
        except (ValueError, TypeError) as exc:
            raise DataError("invalid_response", "Blockstream metadata is not valid JSON/text") from exc
        self.http.session.check_deadline()
        return value

    def btc(self, path, as_json=True, secondary=False):
        apis = BTC_APIS[1:] if secondary else BTC_APIS
        errors = []
        for api in apis:
            try:
                raw = self.http.read(api + path)
                result = json.loads(raw) if as_json else raw.decode().strip()
                self.http.session.check_deadline()
                self.last_btc_api = api
                return result
            except BudgetError:
                raise
            except (DataError, ValueError) as exc:
                errors.append(str(exc))
        raise DataError("source_unavailable", "; ".join(errors))

    def timestamp(self, stamp):
        if self.bitcoin_conflict: raise DataError("source_conflict", self.bitcoin_conflict["message"])
        if not self.timestamp_failed:
            try:
                value = json.loads(self.http.read(BTC_APIS[1] + "/v1/mining/blocks/timestamp/" + str(stamp), 65536))
                self.http.session.check_deadline()
                if (not isinstance(value, dict) or isinstance(value.get("height"), bool)
                        or not isinstance(value.get("height"), int) or value["height"] < 0):
                    raise DataError("invalid_response", "Unsupported Bitcoin timestamp response")
                hex32(value["hash"])
                meta = self.btc("/block/" + value["hash"])
                canonical = self.btc("/block-height/" + str(value["height"]), False)
                if meta["id"] != value["hash"] or meta["height"] != value["height"] or canonical != value["hash"]:
                    self.conflict("Timestamp hint disagrees with Bitcoin height/hash metadata")
                previous = self.timestamp_seeds.get(value["height"])
                if previous is not None and previous != value["hash"]:
                    self.conflict("Timestamp hints disagree at the same Bitcoin height")
                self.timestamp_seeds[value["height"]] = value["hash"]
                self.timestamp_events.append({"at": now(), "kind": "primary_timestamp_hint", "source": BTC_APIS[1], "target_timestamp": stamp})
                return {"lookup": value, "block": meta, "source": BTC_APIS[1], "global_timestamp_floor_proven": False}
            except BudgetError:
                raise
            except (DataError, ValueError, KeyError, TypeError) as exc:
                if self.bitcoin_conflict or getattr(exc, "code", "") in ("source_conflict", "inconsistent_data"):
                    raise
                self.timestamp_failed = True
                self.timestamp_events.append({"at": now(), "kind": "timestamp_primary_unavailable", "source": BTC_APIS[1], "reason": str(exc)})
                self.progress("Using the fallback timestamp source")
        try:
            if self.timestamp_recovery is None:
                from .timestamps import TimestampRecovery
                self.timestamp_recovery = TimestampRecovery(self.pinned_bitcoin, self.http.session.check_deadline)
                for height, digest in self.timestamp_seeds.items():
                    self.timestamp_recovery.seed(height, digest)
            self.progress("Estimating the Bitcoin search range using fallback metadata")
            hint = self.timestamp_recovery.hint(stamp)
        except DataError as exc:
            if exc.code == "source_conflict": self.bitcoin_conflict = {"code": exc.code, "message": str(exc)}
            raise
        self.timestamp_events.append({"at": now(), "kind": "timestamp_fallback_hint", "source": BTC_APIS[0], "target_timestamp": stamp,
                                      "height": hint["lookup"]["height"], "boundary_mode": hint["recovery"]["boundary_mode"]})
        return hint

    def block_raw(self, block_hash, max_bytes):
        errors = []
        for api in BTC_APIS:
            try:
                return self.http.read(api + "/block/" + block_hash + "/raw", max_bytes, block=True), api
            except BudgetError:
                raise
            except DataError as exc:
                errors.append(str(exc))
        raise DataError("source_unavailable", "; ".join(errors))
