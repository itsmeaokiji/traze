"""Operator-selected lifetime ceiling for explicit local browser discovery."""
import argparse
import re

from .errors import DataError
from .storage import SAFETY_MARGIN

MAX_SAFE_BYTES = 2**53 - 1


def validate_ceiling(value):
    if type(value) is not int or not SAFETY_MARGIN < value <= MAX_SAFE_BYTES:
        raise DataError('invalid_discovery_limit', 'Use a finite whole-byte discovery ceiling above the safety margin and within the supported integer range.')
    return value


def megabytes(value):
    """argparse conversion; decimal MB, never floats, infinity or an implicit reset."""
    try:
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}', value):
            raise ValueError()
        return validate_ceiling(int(value) * 1_000_000)
    except (ValueError, DataError):
        raise argparse.ArgumentTypeError('Use a whole number of MB greater than 1 and within the supported integer range.') from None
