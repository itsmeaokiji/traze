"""Bounded Blockstream timestamp hints; MTP ordering does not order raw timestamps."""

import struct

from .bitcoin import hex32, display_hash, sha256d, header_pow
from .errors import DataError, BudgetError

SOURCE = "https://blockstream.info/api"
GENESIS = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"


class TimestampRecovery:
    def __init__(self, fetch, check):
        self.fetch, self.check = fetch, check
        self.cache, self.controls = {}, {}
        self.headers, self.rows = {}, {}
        self.tip = None

    def validate(self, value, height, digest):
        try:
            if not isinstance(value, dict) or value["height"] != height or value["id"] != digest:
                raise ValueError()
            hex32(digest)
            for key in ("height", "timestamp", "mediantime"):
                if isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 0:
                    raise ValueError()
            if value["mediantime"] > value["timestamp"]:
                raise ValueError()
        except (KeyError, TypeError, ValueError, DataError) as exc:
            raise DataError("invalid_response", "Blockstream metadata lacks valid height/hash/time fields") from exc
        return value

    def remember(self, value, height, digest):
        value = self.validate(value, height, digest)
        if height in self.cache and (self.cache[height]["id"] != digest
                or any(self.cache[height][k] != value[k] for k in ("timestamp", "mediantime"))):
            raise DataError("source_conflict", "Bitcoin height/hash/time changed during timestamp recovery")
        row = self.rows.get(height)
        if row and (row["hash"] != digest or row["timestamp"] != value["timestamp"]):
            raise DataError("source_conflict", "Bitcoin timestamp neighborhood changed during recovery")
        self.cache[height] = dict(value)
        ordered = sorted(self.cache)
        if any(self.cache[a]["mediantime"] > self.cache[b]["mediantime"] for a, b in zip(ordered, ordered[1:])):
            raise DataError("source_conflict", "Sampled Bitcoin median times are inconsistent across heights")
        return self.cache[height]

    def block(self, height, fresh=False):
        self.check()
        if not fresh and height in self.cache: return self.cache[height]
        digest = self.fetch("/block-height/" + str(height), False)
        return self.remember(self.fetch("/block/" + digest), height, digest)

    def seed(self, height, digest):
        """Revalidate a prior primary hint on the pinned fallback source."""
        self.check()
        hex32(digest)
        if type(height) is not int or not 0 <= height <= self.anchor()["height"]:
            raise DataError("source_conflict", "Prior timestamp hint is outside the fallback tip view")
        if self.block(height, fresh=True)["id"] != digest:
            raise DataError("source_conflict", "Primary timestamp hint differs from the fallback height view")

    def anchor(self):
        if self.tip is None:
            digest = self.fetch("/blocks/tip/hash", False)
            raw = self.fetch("/block/" + digest)
            height = raw.get("height") if isinstance(raw, dict) else None
            if isinstance(height, bool) or not isinstance(height, int) or not 0 <= height <= 0xFFFFFFFF:
                raise DataError("invalid_response", "Unsupported Bitcoin tip height")
            self.tip = self.validate(raw, height, digest)
            self.cache[height] = self.tip
            genesis = self.block(0)
            if genesis["id"] != GENESIS:
                raise DataError("wrong_network", "Timestamp metadata is not anchored to Bitcoin mainnet genesis")
        return self.tip

    def check_anchor(self):
        self.check()
        current = self.fetch("/block-height/" + str(self.tip["height"]), False)
        if current != self.tip["id"]:
            raise DataError("source_conflict", "Observed Bitcoin tip anchor changed during timestamp recovery")

    def neighborhood(self, start, end):
        for batch_end in range(end, start - 1, -10):
            self.check()
            needed = range(max(start, batch_end - 9), batch_end + 1)
            if all(height in self.rows for height in needed):
                continue
            batch = self.fetch("/blocks/" + str(batch_end))
            if not isinstance(batch, list) or not 1 <= len(batch) <= 10:
                raise DataError("invalid_response", "Expected a bounded Bitcoin block metadata list")
            seen = set()
            for value in batch:
                try:
                    height, stamp, digest = value["height"], value["timestamp"], value["id"]
                    if (type(height) is not int or not max(0, batch_end - 9) <= height <= batch_end
                            or height in seen or isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0):
                        raise ValueError()
                    hex32(digest)
                except (ValueError, KeyError, TypeError, DataError) as exc:
                    raise DataError("invalid_response", "Malformed Bitcoin timestamp neighborhood") from exc
                row = {"height": height, "hash": digest, "timestamp": stamp}
                seen.add(height)
                if height in self.rows and self.rows[height] != row:
                    raise DataError("source_conflict", "Conflicting Bitcoin neighborhood records at one height")
                if height in self.cache and (self.cache[height]["id"] != digest
                        or self.cache[height]["timestamp"] != stamp):
                    raise DataError("source_conflict", "Bitcoin neighborhood differs from checked metadata")
                self.rows[height] = row
                # Esplora lists normally include median time. Older retained
                # lists without it remain usable as raw-time hints only.
                if "mediantime" in value:
                    self.remember(value, height, digest)
            if seen != set(range(max(0, batch_end - 9), batch_end + 1)):
                raise DataError("incomplete_neighborhood", "Bitcoin timestamp metadata batch has gaps")
        if not all(height in self.rows for height in range(start, end + 1)):
            raise DataError("incomplete_neighborhood", "Bitcoin timestamp neighborhood has gaps")
        return [dict(self.rows[n]) for n in range(start, end + 1)]

    def header(self, digest):
        self.check()
        if digest not in self.headers:
            try:
                raw = bytes.fromhex(self.fetch("/block/" + digest + "/header", False))
            except (ValueError, TypeError) as exc:
                raise DataError("invalid_response", "Bitcoin timestamp control header is malformed") from exc
            if len(raw) != 80 or display_hash(sha256d(raw)) != digest or not header_pow(raw):
                raise DataError("source_conflict", "Timestamp control header/hash/stated target differs")
            self.headers[digest] = raw
        return self.headers[digest]

    def median_control(self, height):
        self.check()
        if height in self.controls: return self.controls[height]
        boundary = self.block(height, fresh=True)
        headers = []
        digest = boundary["id"]
        # Each child header commits to its parent. Walk those hashes backwards
        # from a fresh height anchor instead of fetching metadata for ten parents.
        for number in range(height, max(-1, height - 11), -1):
            self.check()
            raw = self.header(digest)
            stamp = struct.unpack_from("<I", raw, 68)[0]
            meta = self.cache.get(number)
            row = self.rows.get(number)
            if (meta and (meta["id"] != digest or meta["timestamp"] != stamp)
                    or row and (row["hash"] != digest or row["timestamp"] != stamp)
                    or number == 0 and digest != GENESIS):
                raise DataError("source_conflict", "Timestamp control header/hash/stated target/time differs")
            headers.append({"height": number, "hash": digest, "header_hex": raw.hex(), "timestamp": stamp})
            digest = display_hash(raw[4:36])
        headers.reverse()
        if any(display_hash(bytes.fromhex(child["header_hex"])[4:36]) != parent["hash"]
               for parent, child in zip(headers, headers[1:])):
            raise DataError("source_conflict", "Timestamp control headers do not link")
        median = sorted(h["timestamp"] for h in headers)[len(headers)//2]
        if median != boundary["mediantime"]:
            raise DataError("source_conflict", "Bitcoin metadata median differs from supplied header timestamps")
        control = {"height": height, "mediantime": median, "supplied_headers": headers,
                   "full_spv_or_expected_difficulty_verified": False}
        self.controls[height] = control
        return control

    def hint(self, stamp):
        self.check()
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
            raise DataError("invalid_input", "Timestamp hint requires nonnegative whole seconds")
        tip, genesis = self.anchor(), self.block(0)
        mode, steps = "mtp_bracketed_neighborhood", []
        if stamp < genesis["timestamp"]:
            low = high = 0
            mode = "clamped_before_genesis"
        elif stamp >= tip["mediantime"]:
            low = high = tip["height"]
            mode = "observed_tip_neighborhood"
        else:
            # Previously checked median times give a safe tighter bracket;
            # individual raw block timestamps must never be binary-searched.
            low = max(h for h, meta in self.cache.items() if meta["mediantime"] <= stamp)
            high = min(h for h, meta in self.cache.items() if meta["mediantime"] > stamp)
            while high - low > 1:
                self.check()
                middle = (low + high) // 2
                meta = self.block(middle)
                steps.append({"height": middle, "mediantime": meta["mediantime"]})
                if meta["mediantime"] <= stamp: low = middle
                else: high = middle
                if len(steps) > 32: raise BudgetError("Timestamp metadata search step bound reached")
            if not self.block(low)["mediantime"] <= stamp < self.block(high)["mediantime"]:
                raise DataError("source_conflict", "Observed Bitcoin median times do not bracket the target")
        start, end = max(0, low - 12), min(tip["height"], high + 2)
        rows = self.neighborhood(start, end)
        before = [r for r in rows if r["timestamp"] <= stamp]
        after = [r for r in rows if r["timestamp"] > stamp]
        if mode == "clamped_before_genesis": selected = rows[0]
        elif before:
            selected = max(before, key=lambda r: r["height"])
            if selected["height"] == tip["height"] and not after: mode = "clamped_to_observed_tip"
            elif not after and mode == "mtp_bracketed_neighborhood":
                raise DataError("unbracketed_timestamp", "Bounded Bitcoin raw timestamps do not bracket the target")
        else:
            raise DataError("unbracketed_timestamp", "No timestamp candidate in the bounded Bitcoin neighborhood")
        meta = self.block(selected["height"], fresh=True)
        if meta["id"] != selected["hash"] or meta["timestamp"] != selected["timestamp"]:
            raise DataError("source_conflict", "Selected timestamp hint disagrees with canonical height metadata")
        control = self.median_control(low)
        self.check_anchor()
        return {"lookup": {"height": selected["height"], "hash": selected["hash"], "timestamp": selected["timestamp"]},
                "block": meta, "source": SOURCE, "global_timestamp_floor_proven": False,
                "recovery": {"kind": "blockstream_mtp_neighborhood", "boundary_mode": mode,
                    "mtp_boundary": [low, high], "mtp_steps": steps, "metadata_neighborhood": [start, end],
                    "observed_raw_timestamps": rows, "mtp_header_control": control,
                    "tip_anchor": {"height": tip["height"], "hash": tip["id"]},
                    "not_a_protocol_time_or_publication_lower_bound": True}}
