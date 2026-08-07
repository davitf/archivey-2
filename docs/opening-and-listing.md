# Opening and listing

Open an archive — unlocking it first if it needs a password — and find out what is
inside. Reading the bytes out is [Reading members](reading-members.md).

## Open and list

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:                    # archive order
        print(member.name, member.size, member.type)

    members = reader.members()               # the full list, or an error
    info = reader.get("subdir/a.txt")        # by name
    print(reader.format, reader.cost)
```

**By default you can open any member you like, in any order.** That is what most
callers want, and it is what the example above relies on. It is not always the
cheapest way to read, though — [Reading members](reading-members.md#two-ways-to-read)
covers when to make one forward pass instead.

If your source is a pipe or another non-seekable stream, pass `streaming=True` for a
forward-only single pass. Without it the open fails immediately rather than halfway
through — see [What you can open](#what-you-can-open) for which formats can be read
this way at all.

### `open_archive` or `open_stream`?

A `.gz`, `.bz2`, `.xz`, `.zst` and so on holds one compressed payload, with no
archive structure around it. Either entry point handles that; they differ in what you
get back:

```python
archivey.open_archive("logs.tar.gz")   # an archive: the files inside the tar
archivey.open_stream("access.log.gz")  # a stream: the decompressed bytes
```

`open_archive` works on a plain `.gz` too — you get an archive with exactly one
member, named after the file. Use `open_stream` when you just want the bytes and
know there is no tar inside.

## What you can open

| Source | What happens |
|---|---|
| A path to a file | Detected and opened |
| A path to a directory | Opens as a pseudo-archive, one member per file |
| An open binary stream | Any format if it is seekable; only some formats if not — see below |
| A sequence of paths or streams | The volumes of one multi-volume archive — see below |

Passing a `format=` that says anything other than a directory, for a path that is one,
raises `ArchiveyUsageError` rather than quietly reading the directory tree instead.

**A seekable stream is read from wherever it currently is**, through to the end.
Archivey treats the current position as byte 0 of the archive, so an archive stored
at a known offset inside a larger file opens without copying it out: seek to its
first byte and hand the stream over. There is no matching end bound, so this works
when the archive runs to the end of the stream; if something follows it, wrap the
stream in your own bounded view first.

**A non-seekable stream** — a pipe, a socket, an HTTP response body — needs
`streaming=True`, and works for TAR (including compressed tar) and the single-file
compressors. ZIP, 7z, RAR and ISO keep their index at the end of the archive or
address it by offset, so they have to seek: opening one from a pipe raises
`StreamNotSeekableError`, and the fix is to buffer it to a file or a `BytesIO` first.

**You do not have to find that out by trying.** `format_availability(fmt).required_source`
is the weakest source shape the format can be read from, so "pipe it if you can,
otherwise spool it to disk" is a comparison rather than a `try`/`except`:

```python
from archivey import StreamCapability, detect_format, format_availability

if format_availability(detect_format(head)).required_source <= StreamCapability.FORWARD_ONLY:
    ...  # feed the pipe straight in with streaming=True
else:
    ...  # spool to a file first
```

`StreamCapability` is ordered (`FORWARD_ONLY < SEEKABLE`), which is why `<=` reads as
"this source is strong enough" — and why the same comparison works against an already
open archive's `reader.cost.stream_capability`.

### Multi-volume archives

Only 7z and RAR split across volumes. **Pass the path of any one volume and Archivey
finds the rest**, in the naming schemes those tools produce:

| Scheme | Give it |
|---|---|
| `backup.7z.001`, `.002`, … | Any part |
| `backup.part1.rar`, `.part2.rar`, … | Any part |
| `backup.rar` + `backup.r00`, `.r01`, … | The `.rar`, or any `.rNN` |

A 7z set is checked for completeness, so a missing middle part is an error rather
than a silent short read. The old RAR scheme needs its `.rar` present either way: that
file is volume one, so a `.rNN` on its own is read as a lone file rather than as part
of a set.

You can also pass the volumes yourself, as an ordered sequence of paths or open
streams — useful when they are not siblings on disk, or not on disk at all. Do that
and the order you give is the order used, with no discovery. A one-item sequence is
treated as a single source, and a multi-volume sequence for any format other than 7z
or RAR raises.

## Detection

Most callers never need this: `open_archive` detects the format itself. Use
`detect_format` when you want to know what a file is *before* deciding to open it.

```python
info = archivey.detect_format("mystery.bin")
print(info.format, info.confidence)
```

**Content wins over filename.** Archivey looks at the bytes first and falls back to
the extension only when they are inconclusive. When the two disagree it uses the
bytes and tells you, via a `FORMAT_EXTENSION_CONFLICT`
[diagnostic](errors-and-diagnostics.md) naming both candidates — a `.jpg` that is
really a ZIP opens fine, and you can still find out that the name lied.

`detect_format` reports the same format `open_archive` would use, with one wrinkle
worth knowing. Telling a `.tar.zst` from a plain `.zst` means decompressing a little
of it to look for the tar header, so when that compressor's package is not installed
the check cannot run and the bare compressor is reported instead. You are not left
guessing: opening the file raises `UnsupportedFormatError`, naming the package to
install.
See [Install and extras](install.md#what-each-format-needs).

## Passwords

```python
archivey.open_archive("secret.7z", password="hunter2")
archivey.open_archive("secret.zip", password=["likely", "fallback"])
```

Put the most likely password first: every wrong candidate costs work before it is
rejected, which can be expensive (especially on 7z).

Passing a password to a format that has no encryption at all — a tar, say — is
**accepted and never consulted**, and records a `PASSWORD_ARGUMENT_UNUSED` diagnostic
you can query on `reader.diagnostics`. That is deliberate. `password=` is a *keyring you are offering*, not a claim that this
archive is encrypted — "here are the twenty passwords we know, open whatever you can"
is the point of the list form — so one plain `.tar` in a batch should not stop the run.
All three forms behave alike here: a string, a list, and a `PasswordProvider` callable.

A *wrong* password on an archive that really is encrypted still fails loudly with
`EncryptionError`, which is the case that actually costs you something.

## Damaged archives

`members()` and `scan_members()` give you the whole listing or raise — if the archive
is damaged partway through, you get an error, never a quietly shortened list.
`members_report()` is the other half of that deal: it hands back the members it did
manage to read *together with* the error that stopped it. Iterating yields members up
to the damage and then raises.

[Errors and diagnostics](errors-and-diagnostics.md#listing-a-damaged-archive) has the
recipe and what each failure means.

## Duplicate names and is_current

Appending to a tarball, or updating a 7z, can leave **the same member name in the
archive more than once**. Archivey never hides the older copies — `members()` and
iteration return every entry — but it marks which one is live:

- The **last** entry with a given name has `is_current=True`.
- Earlier entries with that name have `is_current=False`.

`reader.get(name)` and `reader.open(name)` follow the same rule: a name resolves to
the last entry, which is the live one.

`extract_all` also follows it. Superseded entries are skipped and reported as
`ExtractionStatus.SUPERSEDED` (distinct from `NOT_OVERWRITTEN`, which is about files
already on disk), so what lands on disk matches a fresh write.

**Selecting members by name is the one place to be careful.** A name in a selector —
`extract_all(members=["notes.txt"])`, `stream_members(members=["notes.txt"])` —
matches *every* entry with that name, not just the live one. For `extract_all` that
is harmless, since the superseded ones are skipped anyway; `stream_members` has no
such skip and will hand you each version in turn. Pass the `ArchiveMember` itself
when you mean one specific entry — selectors match those by identity.

In your own code, filter for the live state:

```python
with archivey.open_archive("updated.tar") as reader:
    current = [m for m in reader if m.is_current]
```

Or keep every version, for a history view:

```python
with archivey.open_archive("history.tar") as reader:
    for member in reader:
        tag = "" if member.is_current else " [superseded]"
        print(f"{member.name}{tag}")
```
