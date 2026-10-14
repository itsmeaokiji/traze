"""Shared number/hash input validation; malformed input starts no public reads."""

import re

from .errors import DataError

HASH_INPUT = re.compile(r"0x[0-9a-f]{64}\Z", re.IGNORECASE)


def covering(number):
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 0xFFFFFFFF:
        raise DataError("unsupported_input", "Use a positive Hemi block number supported by the v1 codec; genesis is excluded")
    value = (number + 24) // 25 * 25
    if value > 0xFFFFFFFF:
        raise DataError("unsupported_input", "Covering checkpoint exceeds the v1 integer range")
    return value


def block_input(value):
    if isinstance(value, int) and not isinstance(value, bool):
        covering(value)
        return value
    if isinstance(value, str):
        text = value.strip()
        if HASH_INPUT.fullmatch(text):
            return text.lower()
        if len(text) <= 66 and (re.fullmatch(r"[0-9]+", text) or re.fullmatch(r"0x[0-9a-f]+", text, re.IGNORECASE)):
            number = int(text, 16 if text.lower().startswith("0x") else 10)
            covering(number)
            return number
    raise DataError("invalid_block", "Enter a positive Hemi block number or a full 0x-prefixed 64-digit block hash")
