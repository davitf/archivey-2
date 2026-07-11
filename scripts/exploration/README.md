# ZipCrypto disambiguation — exploration notes

Scripts supporting
`openspec/changes/zip-multipassword-disambiguation` tasks 1.1 and 1.3.

| Script | Purpose |
|--------|---------|
| `zipcrypto_codec_rejection.py` | How quickly stdlib DEFLATE / BZIP2 / LZMA reject random and wrong-key ZipCrypto plaintext |
| `zipcrypto_compressibility_probe.py` | Historical calibration of a STORED compressibility probe (investigated, then **dropped** — see `design.md`) |

```bash
uv run --no-sync python scripts/exploration/zipcrypto_codec_rejection.py
uv run --no-sync python scripts/exploration/zipcrypto_compressibility_probe.py
```

Findings are recorded in
`openspec/changes/zip-multipassword-disambiguation/design.md`
(section **Investigation findings**). Runtime STORED confirmation uses a shared CRC
pass only; the compressibility script is kept as a record, not as a live dependency.
