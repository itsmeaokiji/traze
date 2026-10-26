"""Bounded execution segments; timestamp windows are search hints, not proofs."""

from .errors import DataError

SEGMENT_HEIGHTS = 20
WINDOW_FIRST_POLICY = 'estimated_window_first_v1'


def estimated_window(receipt):
    """Read additive new-policy bounds without changing legacy saved scopes."""
    discovery = receipt.get('discovery')
    if discovery:
        window = discovery.get('estimated_window')
        extent = discovery.get('original_extent')
    else:
        plan = receipt.get('coverage', {}).get('plan', {})
        if plan.get('segment_policy') != WINDOW_FIRST_POLICY:
            return None
        if plan.get('mode') != 'timestamp_guided':
            raise DataError('continuation_changed', 'Estimated-window policy needs a timestamp-guided search')
        window = [plan.get('start'), plan.get('initial_end')]
        extent = [plan.get('start'), receipt.get('bitcoin_tip_before', {}).get('height')]
    if window is None:
        return None
    if (not isinstance(window, list) or len(window) != 2 or any(type(h) is not int for h in window)
            or not isinstance(extent, list) or len(extent) != 2 or any(type(h) is not int for h in extent)
            or not 0 <= extent[0] == window[0] <= window[1] <= extent[1]):
        raise DataError('continuation_changed', 'Estimated window is outside the frozen search extent')
    return list(window)
