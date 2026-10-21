"""Minimal standard-library consumer of the indexed read API."""

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765', help='Traze server origin')
    parser.add_argument('--block', help='Optional Hemi number or full hash present in the index')
    args = parser.parse_args(argv)
    origin = urlsplit(args.url)
    if origin.scheme not in ('http', 'https') or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('', '/'):
        parser.error('Use a server origin without credentials, path, query or fragment.')
    base = args.url.rstrip('/')

    def read(path):
        with urlopen(Request(base + path, headers={'Accept': 'application/json'}), timeout=15) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('API response exceeds the example consumer limit')
        body = json.loads(raw)
        if body.get('api_version') != 'v1':
            raise ValueError('Expected the v1 API contract')
        return body

    try:
        index = read('/api/v1/index')
        page = read('/api/v1/publications?limit=1')
        result = {'index_kind': index['index_kind'], 'counts': index['counts'],
                  'bitcoin_height_bounds': index['bitcoin_height_bounds'],
                  'bounds_are_contiguous_coverage': False,
                  'source_observations_current': index['source_observations_current'],
                  'publication_sample': page['items'], 'next_cursor': page['page']['next_cursor']}
        if args.block:
            block = read('/api/v1/blocks/' + quote(args.block, safe=''))
            result['block'] = {'record_url': block['record_url'], 'query': block['receipt']['query'],
                               'proofs_checked': block['verification']['proofs_checked'],
                               'current_chain_membership_checked': block['verification']['current_chain_membership_checked']}
        print(json.dumps(result, indent=2))
        return 0
    except HTTPError as exc:
        body = exc.read(4096).decode('utf-8', errors='replace')
        print(json.dumps({'http_status': exc.code, 'error': body, 'retry_after': exc.headers.get('Retry-After')}))
    except (URLError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
