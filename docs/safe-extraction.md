# Safe extraction

Archivey extracts **safely by default**. You opt *out* of protections; you do not opt in.

## One-shot

```python
archivey.extract("archive.zip", "out/")
# policy=ExtractionPolicy.STRICT, overwrite=ERROR, on_error=STOP
```

## What is already enforced

- **Path traversal** — `..`, absolute paths, drive letters, UNC, null bytes rejected
- **Symlink escapes** — lexical check, parent resolution, post-create re-resolution
- **Hardlink targets** — containment-checked; resolved positionally
- **Never write through a symlink** — replace, don’t follow; atomic temp + `os.replace`
- **Special files** — devices, FIFOs, sockets rejected; NTFS junctions not traversed
- **Decompression bombs** — cumulative bytes, per-member and archive ratios, entry cap
- **Permission hygiene** — setuid/setgid/sticky stripped except under `TRUSTED`

Atomic file writes stage into temp siblings named `.archivey-tmp-<random>` inside the
destination directory. Any Python-level failure removes them; only a hard kill
(SIGKILL, power loss) can leave one behind. Leftover `.archivey-tmp-*` files in an
extraction destination are archivey's staging files and are safe to delete before
re-running the extraction.

Full trust boundaries and open gaps: [threat model](internal/threat-model.md).

## Policies

```python
from archivey import ExtractionPolicy, OverwritePolicy, OnError, ExtractionLimits

archivey.extract(
    "archive.zip",
    "out/",
    policy=ExtractionPolicy.STRICT,       # default
    overwrite=OverwritePolicy.ERROR,      # or REPLACE / SKIP
    on_error=OnError.STOP,                # or CONTINUE
    limits=ExtractionLimits(...),         # or ExtractionLimits.UNLIMITED
)
```

| Policy | Intent |
| --- | --- |
| `STRICT` | Untrusted archives (default) |
| `TRUSTED` | Allow ownership / sticky bits when running as root; still no traversal |

Selective extract on an open reader:

```python
with archivey.open_archive("a.zip") as reader:
    reader.extract_all("out/", members=["only/this.txt"])
```

## Limits

Defaults (via `ExtractionLimits` / `ArchiveyConfig`) cap total extracted bytes, compression
ratio, and entry count. Loosen per call with `limits=`, or use
`ExtractionLimits.UNLIMITED` for trusted inputs you control.

Bomb guards apply during **extraction**. Listing a pathological central directory is a
separate concern (see threat-model gap O1) — prefer progressive iteration for huge
untrusted archives when you only need a subset of members.

## Diagnostics

Structured advisories are queryable on the reader / extraction report (not only logs).
See the `diagnostics` capability and the [API reference](api.md).
