# Reading members

Getting bytes out of a member, and what each outcome means. Finding out what is
*in* the archive is [Opening and listing](opening-and-listing.md); what an access
pattern *costs* is [Access costs and pitfalls](access-and-cost.md).

## Read a member

```python
with archivey.open_archive("photos.zip") as reader:
    with reader.open("subdir/a.txt") as stream:
        data = stream.read()

    data = reader.read("subdir/a.txt")   # the same thing, in one call
```

`read()` has no size limit — it returns the whole member, however large that turns
out to be. That is fine for archives you produced; for anything untrusted, read in
chunks and stop when you have had enough. See
[Limits](extracting.md#limits).

Two defaults keep the common case cheap, and each can be lifted at open time:

- Member streams are **forward-only**. `seek()` raises unless you opened with
  `seekable_members=True`.
- **One stream may be live at a time.** Opening a second while the first is still
  open raises `ConcurrentAccessError` unless you opened with
  `concurrent_members=True`.

```python
with archivey.open_archive(
    "data.zip",
    seekable_members=True,
    concurrent_members=True,
) as reader:
    ...
```

Neither flag is free — see [Access costs](access-and-cost.md#concurrent-member-streams).

## Two ways to read

`reader.open(member)` reads one member, whichever you ask for.
`reader.stream_members()` walks the whole archive in order, handing you each member
with its stream:

```python
with archivey.open_archive("backup.tar.zst") as reader:
    for member, stream in reader.stream_members():
        if stream is None:
            continue                     # a directory or other non-file entry
        print(member.name, len(stream.read()))
```

**Which one to use depends on the format, and `reader.cost.access_cost` tells you
which case you are in.** For a ZIP or an uncompressed TAR it is `DIRECT`: members are
stored independently, so opening one costs the same whether you read one member or
all of them. Use whichever is convenient.

For a solid 7z or RAR, or any compressed tar (`.tar.gz`, `.tar.zst`, …), it is
`SOLID`: members are compressed together as one run, so reading the member in the
middle means decompressing everything before it. Do that once per member and a linear
read becomes a quadratic one. `stream_members()` decodes the run once, in order, and
hands you each member as it goes. Nothing warns you about this one — it is slow, not
wrong — so check `access_cost` rather than waiting to notice.

Three things about the yielded streams are worth knowing:

- **The stream is only valid until you advance.** The iterator closes it before
  producing the next pair, so read what you need in the loop body. Keeping a
  reference and reading it later gets you a closed stream, not stale data.
- **Non-file members yield `None`.** Directories, symbolic links and hard links all
  come through as `(member, None)` — hence the `if stream is None` above.
- **Nothing is decompressed until you read.** A member you skip is never opened, and
  no password is requested for it. So "I iterated the whole archive without an error,
  therefore the password is right" does not follow: pass a selector, or read each
  stream, if you want the archive actually checked.

    This applies to **data** encryption — the common case, where the member list is
    readable and only the payloads are ciphertext. It does not apply to
    **header**-encrypted 7z and RAR, where the listing itself is encrypted: there the
    password is needed at `open_archive()` and opening without one raises
    `EncryptionError` before you get a member to skip. That is format law, not a
    laziness choice — see [Formats](formats.md).

Links are the one to watch, because `reader.open()` *does* follow them. Following a
link means reading the target's bytes, and those live somewhere else in the archive —
in a single forward pass that position may already be behind you. Formats that could
reach it anyway follow the same rule, so the loop body does not change shape from one
archive to the next. A loop that skips every `None` therefore skips links, where the
same loop written around `open()` would hand you the target's contents. Read
`member.link_target` if you need to resolve them yourself, or let
[extraction](extracting.md) recreate the links for you.

A `stream_members()` pass owns the reader while it runs. Calling `open()`,
`members()` or another pass inside the loop raises `ArchiveyUsageError` rather than
quietly disturbing the pass.

## What a read gives you back

Reading a member to its end verifies it where the archive stores a checksum, and
raises rather than quietly handing you short or wrong data. Two things are worth
knowing here; the full contract is on
[Errors and diagnostics](errors-and-diagnostics.md#the-integrity-guarantee).

- **`read(member.size)` behaves differently for the two failures.** On corruption it
  raises and withholds the chunk that reached the size. On truncation it returns a
  **short buffer with no exception** — known-wrong bytes are held back, an apparently
  incomplete prefix is handed over. So a short return from that call is a signal:
  check the length.
- **The ordinary chunked loop is safe.** It delivers every readable byte and *then*
  raises, so it cannot end quietly on a damaged member:

```python
buf = bytearray()
try:
    with reader.open("member.bin") as stream:
        while chunk := stream.read(1 << 20):
            buf.extend(chunk)
except archivey.ReadError:
    ...  # buf holds everything that was readable; the member is damaged
```

A plain `stream.read()` with no argument asks for the whole member, so a damaged one
raises and you get nothing back. Use the loop above when a partial result is worth
having.

## Which members you can open

**Symbolic and hard links are followed**, so opening one gives you the target's
bytes. A broken link raises `LinkTargetNotFoundError`, and a cycle raises rather than
spinning. `stream_members()` is the exception: it yields links as `(member, None)`,
for the reason given above.

**Directories and other non-file entries cannot be opened.** `reader.open()` on one
raises `ArchiveyUsageError` naming the type — check `member.type` first, or use the
`stream is None` test that `stream_members()` gives you.

**A member belongs to the reader that produced it.** Passing an `ArchiveMember` from
a different archive raises `ArchiveyUsageError` rather than resolving it against the
wrong offsets and returning the wrong bytes. For the same reason `member in reader`
tests identity, not name: giving it a string raises `TypeError` and points you at
`reader.get(name)`, rather than falling back to a scan that would consume a streaming
pass.

**A member stream does not outlive its reader.** Closing the reader closes any member
streams still open on it, the same way `zipfile.ZipFile.close()` and
`tarfile.TarFile.close()` do — so reading one afterwards raises, as it would for any
closed file. Nesting `with` blocks is still the clearest way to write it, and it means
you never depend on the order:

```python
with archivey.open_archive("photos.zip") as reader:
    with reader.open("subdir/a.txt") as stream:
        data = stream.read()
```

## Streaming mode (pipes)

```python
with archivey.open_archive(sys.stdin.buffer, streaming=True) as reader:
    for member, stream in reader.stream_members():
        ...  # single forward pass
```

`streaming=True` promises one forward pass and nothing more, so the random-access
methods — `members()`, `get()`, `open()`, `read()` — raise
`UnsupportedOperationError`. What you have instead is `__iter__`, `stream_members()`
and `extract_all()`, and you get **one** of them: the first consumes the source, even
if you `break` out early.

## One-shot extract

`archivey.extract(src, dest)` extracts everything with safe defaults — see
[Extracting](extracting.md).

Two things about it are easy to trip over:

- **There is no `members=` argument.** Selecting a subset needs the member list, and
  fetching that would mean opening, listing and reopening inside a call that is meant
  to be one pass. Open a reader and use `reader.extract_all(members=...)` instead.
- **It accepts a non-seekable source**, opening it in streaming mode for you, while
  `open_archive` refuses one unless you pass `streaming=True`. Extraction is a single
  forward pass by nature, so there is nothing to decide.
