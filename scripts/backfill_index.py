"""Opt-in finite backfill and source-free coverage accounting."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hemi_lookup.backfill import main
from hemi_lookup.errors import DataError

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (DataError, OSError, ValueError, KeyError, TypeError) as exc:
        import json
        print(json.dumps({'error': getattr(exc, 'code', 'backfill_error'), 'message': str(exc)}))
        raise SystemExit(2)
