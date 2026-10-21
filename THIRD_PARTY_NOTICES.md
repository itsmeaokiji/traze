# Third-party notices

## Optional production serving dependency

The production adapter can install Waitress 3.0.2 from its checksum-pinned PyPI
wheel. Waitress is licensed under ZPL 2.1 by the Zope Foundation and contributors;
its installed wheel includes its license. It is not bundled into the frontend or
required by the standard-library local demo. See [the upstream project](https://github.com/Pylons/waitress)
and [the pinned runtime](deploy/requirements.txt).

## Hemi protocol and publication codec

The reviewed Hemi v1 keystone and publication rules are referenced/ported from heminetwork at revision bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1:

- [Keystone serialization](https://github.com/hemilabs/heminetwork/blob/bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1/hemi/hemi.go)
- [Publication codec](https://github.com/hemilabs/heminetwork/blob/bf771ced8cfd2fb782c3b0d6d0bc8ea87b1e7cc1/hemi/pop/pop.go)
- [Upstream MIT license](third_party/heminetwork-LICENSE.txt)

The original copyright and permission notice is retained. Source/rule attribution does not establish deployed equivalence or Hemi endorsement.

## Fonts and chain marks

[Frontend asset notices](frontend/THIRD_PARTY_NOTICES.md) retain font licenses, source pins and mark provenance. Local notices include [Geist OFL text](frontend/licenses/geist-OFL.txt), [Geist Mono OFL text](frontend/licenses/geist-mono-OFL.txt) and [Hemi UI MIT text](frontend/licenses/hemi-ui-MIT.txt).

The Hemi logomark identifies Hemi records. The [MIT project license](LICENSE) covers original Traze code and does not replace third-party terms or grant trademark rights. See [licensing](docs/LICENSE_REVIEW.md).

## Frontend runtime and generated helpers

The supplied build includes React, React DOM and Scheduler. Their Meta MIT copyright and permission notice is retained in [React MIT text](frontend/licenses/react-MIT.txt). Core MIT notices for generated Vite/Rolldown helpers are retained in [Vite MIT text](frontend/licenses/vite-MIT.txt) and [Rolldown MIT text](frontend/licenses/rolldown-MIT.txt).

[Frontend notices](frontend/THIRD_PARTY_NOTICES.md) identify versions and materials. [The combined distribution notice](frontend/public/THIRD_PARTY_LICENSES.txt) is copied into web/ during a build so notices accompany the supplied browser code.

## Historical evidence

[The fixture manifest](tests/fixtures/bitcoin_blocks/manifest.json) records byte fingerprints and original observation context. Historical provider observations remain historical; copying fixtures is not new chain confirmation.
