# Retained Bitcoin corpus

This corpus contains 17 historical Bitcoin bodies totaling 25,845,550 bytes, with checksums and original source observations recorded in the manifest.

[manifest.json](manifest.json) records each source-reported height/hash, SHA256 fingerprint, byte count and original receipt observation. The source intervals are Bitcoin 941741–941750 and 965925–965931.

The bodies are historical data fixtures. File checksums are transport/integrity checks, not independent chain consensus. Catalogue import reparses each body, checks the transaction root and extracts the reviewed HEMI-format candidates.

The two source receipts retain their original bytes and source times. Database tests use temporary databases built from this corpus; they do not copy a personal SQLite catalogue.

Keep this directory separate from runtime cache/accounting. Changing a fixture or source receipt requires updating provenance and meaningful checks; never relabel controlled data as a new live observation.
