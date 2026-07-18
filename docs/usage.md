# Basic usage

## Install

```bash
pip install archivey                  # zero-dep core: ZIP, TAR, gz/bz2/xz, directory, …
pip install archivey[recommended]     # formats + codecs + seek accelerators + CLI deps
pip install archivey[recommended-lite]  # same without rapidgzip (when it won’t build)
```

RAR **member data** also needs the system `unrar` binary on `PATH` (listing works without
it). See [Formats and extras](formats.md).

## Open and list

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:                    # archive order
        print(member.name, member.size, member.type)

    members = reader.members()               # random access: full list (cached)
    info = reader.get("subdir/a.txt")        # by name
    print(reader.format, reader.cost)
```

Default open is **random access** (`streaming=False`). On a pipe or other non-seekable
source, either pass a seekable file or use `streaming=True` (forward-only, one pass).

## Read a member

```python
with archivey.open_archive("photos.zip") as reader:
    with reader.open("subdir/a.txt") as stream:
        data = stream.read()
```

By default streams are **forward-only** and only **one** may be live. Need seeking or
overlapping opens? Declare capabilities:

```python
from archivey import MemberStreams

with archivey.open_archive(
    "data.zip",
    member_streams=MemberStreams.SEEKABLE | MemberStreams.CONCURRENT,
) as reader:
    ...
```

## One-shot extract

```python
archivey.extract("photos.zip", "out/")   # all members; safe defaults
```

Selective extraction uses an already-open reader (`reader.extract_all(members=...)`).
There is deliberately no `members=` on the one-shot helper — that would force open /
list / reopen.

## Detect without opening

```python
info = archivey.detect_format("mystery.bin")
print(info.format, info.confidence)
```

## Streaming mode (pipes)

```python
with archivey.open_archive(sys.stdin.buffer, streaming=True) as reader:
    for member, stream in reader.stream_members():
        ...  # single forward pass
```

In streaming mode, `members()` / `get()` / `open()` / `read()` raise
`UnsupportedOperationError`. Use `__iter__`, `stream_members`, or `extract_all` once.

## Cheap dedupe with stored hashes

Prefer digests the archive already stores (`member.hashes`) before computing your own —
see the [stored-digest matrix](formats.md#stored-digests-cheap-dedupe). Recipe:

```python
import hashlib
import archivey
from archivey import HashAlgorithm

def content_key(reader, member):
    """Best available digest for a first-pass dedupe index."""
    if HashAlgorithm.BLAKE2SP in member.hashes:
        return ("stored", "blake2sp", member.hashes[HashAlgorithm.BLAKE2SP])
    if HashAlgorithm.CRC32 in member.hashes:
        return ("stored", "crc32", member.hashes[HashAlgorithm.CRC32])
    # No cheap stored digest (e.g. tar, bzip2): compute while reading.
    h = hashlib.sha256()
    with reader.open(member) as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return ("computed", "sha256", h.digest())

with archivey.open_archive("backups.zip") as reader:
    for member in reader:
        if member.is_file and member.is_current:
            print(member.name, content_key(reader, member))
```

Stored digests are weaker or format-specific; computed digests are stronger but cost a
full decode. Pick by provenance (`stored` vs `computed`) for your index policy.

## Duplicate names and is_current

Appended tarballs, 7z update operations, and similar workflows can produce archives
where **the same member name appears more than once**. Archivey always returns all
entries — `members()` / `__iter__` never hide anything — but marks which one is
"live" with `member.is_current`:

- The **last** entry with a given name has `is_current=True` (last-entry-wins).
- All earlier same-name entries have `is_current=False` (superseded).

`extract_all` honours this automatically: non-current entries get
`ExtractionStatus.SUPERSEDED` (distinct from overwrite `SKIPPED`) and are not written,
so the final on-disk state matches what you would get from a fresh write.

To enumerate only the live state in your own code, filter with a one-liner:

```python
with archivey.open_archive("updated.tar") as reader:
    current = [m for m in reader if m.is_current]
```

If you need all versions (e.g. a history view), iterate without filtering:

```python
with archivey.open_archive("history.tar") as reader:
    for member in reader:
        tag = "" if member.is_current else " [superseded]"
        print(f"{member.name}{tag}")
```

## Passwords

```python
archivey.open_archive("secret.7z", password="hunter2")
archivey.open_archive("secret.zip", password=["likely", "fallback"])
```

List the most likely password first — especially for 7z, where each wrong candidate pays
key derivation.

## Error handling

Every failure that comes from the archive or its environment derives from
[`ArchiveyError`][archivey.ArchiveyError], so one `except` covers them all:

```python
from archivey import open_archive, ArchiveyError

try:
    with open_archive("maybe.7z") as reader:
        reader.extract_all("out/")
except ArchiveyError as e:
    print("could not process archive:", e)
```

React to specific cases with the subtypes:

| Exception | Raised when |
| --- | --- |
| `OpenError` | the source can't be opened — `FormatDetectionError` (unknown format), `UnsupportedFormatError`, `StreamNotSeekableError` (random-access open on a pipe) |
| `EncryptionError` | a password is required, missing, or wrong |
| `CorruptionError` / `TruncatedError` | the archive is malformed or cut short |
| `PackageNotInstalledError` | an optional package or tool is absent (e.g. the `unrar` binary for RAR data) |
| `FilterRejectionError` | extraction blocked an unsafe member — `PathTraversalError`, `SymlinkEscapeError`, `SpecialFileError` |
| `ResourceLimitError` | a listing/extraction safety limit (member count, size) was exceeded |

Mistakes in **your** code are deliberately kept out of that hierarchy: opening a second
overlapping stream without `MemberStreams.CONCURRENT`, using a closed reader, and similar
misuse raise [`ArchiveyUsageError`][archivey.ArchiveyUsageError] (e.g.
`ConcurrentAccessError`), which is **not** an `ArchiveyError` — so a blanket
`except ArchiveyError` never silently swallows a bug. (When an *archive* genuinely can't
provide an operation — seeking a non-seekable member, a format that can't list — that is a
real `ArchiveyError`: `UnsupportedOperationError`.) See
[decision 0012](decisions/0012-usage-errors-outside-archiveyerror.md).

## Command-line interface

The `archivey` command ships with the base package (`pip install archivey`). Progress bars
need the optional `[cli]` extra (`tqdm`); without it the command still runs.

```bash
archivey photos.zip                 # same as: archivey list photos.zip
archivey l photos.zip               # list (alias)
archivey t photos.zip               # full-read integrity check
archivey x photos.zip               # safe extract (alias for extract)
archivey info photos.zip            # format / identity (alias: detect)
```

### Safer extract demo

```bash
# Default policy=strict, overwrite=rename. With no -d, a multi-entry archive lands in
# ./photos/ instead of splattering the current directory (tarbomb-safe).
archivey extract photos.zip

# Classic unzip-into-cwd (opt-in):
archivey extract photos.zip -d .

# Filters: positionals are includes; --exclude subtracts.
archivey extract photos.zip -d out/ '*.py' --exclude '*_test.py'
archivey extract photos.zip --policy trusted -d /tmp/out
```

### Notes

- Verbs are bare words (`x`, `list`); dash-prefixed forms like `-x` are not mode selectors.
- A file whose name is a verb word (e.g. `./x`) is reached with an explicit verb:
  `archivey list ./x`.
- Exit codes: `0` success, `1` operation failed, `2` usage error (argparse). Codes `≥3`
  are reserved.
- `--salvage`, stdin (`-`), and `hash` / `create` / `convert` are reserved for later.

## Next

- [Gotchas](gotchas.md) — if you read one more page after this, make it that one
- [Access costs and pitfalls](costs.md) — solid archives, seeking, concurrency
- [Formats and extras](formats.md) — quirks per format
- [Safe extraction](safe-extraction.md) — policies and limits
- [API reference](api.md)
