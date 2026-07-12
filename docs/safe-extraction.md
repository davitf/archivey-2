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
from archivey import ExtractionPolicy, OverwritePolicy, OnError, ExtractionLimits, ListingLimits

archivey.extract(
    "archive.zip",
    "out/",
    policy=ExtractionPolicy.STRICT,       # default
    overwrite=OverwritePolicy.ERROR,      # or REPLACE / SKIP
    on_error=OnError.STOP,                # or CONTINUE
    limits=ExtractionLimits(...),         # or ExtractionLimits.UNLIMITED
)

with archivey.open_archive(
    "huge.zip",
    config=archivey.ArchiveyConfig(listing_limits=ListingLimits(max_members=10_000)),
) as reader:
    reader.members()  # ResourceLimitError if the central directory is larger

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

Defaults (via `ExtractionLimits` / `ListingLimits` on `ArchiveyConfig`) cap:

- **Extraction bombs** — total extracted bytes, compression ratio, and entry count
  (`ExtractionLimits`). Trips raise `ResourceLimitError`.
- **Listing materialization** — member count and retained metadata bytes
  (`ListingLimits`) on `members()` / `scan_members()` / extract-prep materialization.
  Trips raise `ResourceLimitError`. `stream_members()` stays unguarded by design.

Loosen per call with `limits=` (extraction only), raise `listing_limits` at
`open_archive(config=…)`, or use `ExtractionLimits.UNLIMITED` /
`ListingLimits.UNLIMITED` for trusted inputs you control.

Bomb guards apply during **extraction**. Listing caps apply when a full member list is
materialized — prefer `stream_members()` for huge untrusted archives when you only need
a sequential subset.

## Diagnostics

Structured advisories are queryable on the reader / extraction report (not only logs).
See the `diagnostics` capability and the [API reference](api.md).
