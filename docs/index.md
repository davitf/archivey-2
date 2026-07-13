# Archivey

Archivey reads, streams, and safely extracts ZIP / TAR / RAR / 7z / ISO / directory /
single-file-compressed archives behind one uniform interface.

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:
        print(member.name, member.size)
```

## User guide

1. **[Philosophy](philosophy.md)** — why Archivey exists and the defaults that follow
2. **[Basic usage](usage.md)** — open, list, stream, extract
3. **[Access costs and pitfalls](costs.md)** — hidden decompression costs and how to avoid them
4. **[Formats and extras](formats.md)** — per-format quirks, required libraries, limitations
5. **[Safe extraction](safe-extraction.md)** — what “safe by default” means in practice
6. **[API reference](api.md)** — generated from source
7. **[Acknowledgements](acknowledgements.md)** — libraries, oracles, and design references

## For contributors

- **[Decision log](decisions/index.md)** — why key design choices were made
- **[Internal reference](internal/index.md)** — threat model, codec analysis, known issues
- **[Grab-bag](grab-bag/index.md)** — historical prose, explorations, triage later
- Repo root (not part of this site): `VISION.md`, `PLAN.md`, `IDEAS.md`,
  `CONTRIBUTING.md`; authoritative contracts in `openspec/specs/`
