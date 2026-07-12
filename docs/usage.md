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

## Passwords

```python
archivey.open_archive("secret.7z", password="hunter2")
archivey.open_archive("secret.zip", password=["likely", "fallback"])
```

List the most likely password first — especially for 7z, where each wrong candidate pays
key derivation.

## Next

- [Access costs and pitfalls](costs.md) — solid archives, seeking, concurrency
- [Formats and extras](formats.md) — quirks per format
- [Safe extraction](safe-extraction.md) — policies and limits
- [API reference](api.md)
