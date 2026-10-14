# Frontend runtime and distribution notices

The supplied browser build contains React 19.3.0, React DOM 19.3.0 and Scheduler 0.28.0. All three retain the Meta Platforms, Inc. and affiliates MIT notice in [React MIT text](licenses/react-MIT.txt).

Generated bootstrap/helpers are produced by Vite 8.3.2 and Rolldown 1.2.12. Their core notices are retained in [Vite MIT text](licenses/vite-MIT.txt) and [Rolldown MIT text](licenses/rolldown-MIT.txt).

[The combined distribution notice](public/THIRD_PARTY_LICENSES.txt) includes the original Traze MIT notice, these dependency notices, the font OFL texts and Hemi UI MIT text. Vite copies it into web/ with the browser build. Retain that file when redistributing the generated build.

Runtime source: [React](https://github.com/react/react), [Vite](https://github.com/vitejs/vite), [Rolldown](https://github.com/rolldown/rolldown). Exact dependency versions and integrity values are recorded in package-lock.json.

# Font assets

Traze bundles the unmodified Latin variable WOFF2 subsets of Geist Sans and Geist
Mono, distributed by @fontsource-variable/geist 5.3.0 and
@fontsource-variable/geist-mono 5.3.0. They are served from the local application's
origin; the browser does not request a third-party font service.

The font software is licensed under SIL Open Font License 1.1. Copyright and
license text from each distribution are preserved in
[Geist Sans license](licenses/geist-OFL.txt) and
[Geist Mono license](licenses/geist-mono-OFL.txt).

Primary font project: [Vercel Geist](https://github.com/vercel/geist-font).
Distribution: [Fontsource font files](https://github.com/fontsource/font-files).

# Chain marks

The circular publication route uses the unmodified orange standalone Hemi
logomark from the [official Hemi brand kit](https://hemi.xyz/brand-kit), specifically
[hemi-logo-orange.svg](https://static.hemi.xyz/brand/symbol/hemi-logo-orange.svg).
Its original path, viewBox and #FF4600 fill are retained. The mark identifies
Hemi records; it does not imply Hemi endorsement of Traze.

The Bitcoin mark preserves the path and #E9983D fill of the Hemi portal's
[BtcLogo component](https://github.com/hemilabs/ui-monorepo/blob/2a5175f998f22ff096a755801f8ab68c6906c5cd/portal/components/connectWallets/btcLogo.tsx).
Its added viewBox matches the original 32 × 31 canvas. The component is covered
by the repository's MIT license; its copyright and full notice are retained in
[Hemi UI license](licenses/hemi-ui-MIT.txt).

Local SVG copies and source fingerprints are in src/assets/. ChainMark.tsx
renders their exact reviewed geometry as inline React SVG, preserving original
fills in both themes. Circle outlines and surrounding text use Traze's theme
tokens. No remote image request, SVG script, or HTML injection is needed.
