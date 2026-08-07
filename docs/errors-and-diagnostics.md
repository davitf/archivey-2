# Errors and diagnostics

What gets raised, what gets recorded, and — if an archive turns out to be damaged —
what you can still get out of it.

## The exception tree

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
overlapping stream without `concurrent_members=True`, using a closed reader, and similar
misuse raise [`ArchiveyUsageError`][archivey.ArchiveyUsageError] (e.g.
`ConcurrentAccessError`), which is **not** an `ArchiveyError` — so a blanket
`except ArchiveyError` never silently swallows a bug. (When an *archive* genuinely can't
provide an operation — seeking a non-seekable member, a format that can't list — that is a
real `ArchiveyError`: `UnsupportedOperationError`.)

## Diagnostics

Structured advisories are queryable on the reader and on the extraction report — not
only in logs. Prefer `reader.diagnostics` and the returned `ExtractionReport` over
hoping something appeared in a log handler. See the `diagnostics` capability and the
[API reference](api.md).

### Things that are said with a diagnostic rather than an exception

A handful of conditions are real enough to tell you about and not wrong enough to
refuse. Each has a `DiagnosticCode` you can match on, and any of them can be escalated
to an exception with a `DiagnosticPolicy` if your program would rather stop:

| Code | Means |
| --- | --- |
| `EMPTY_ARCHIVE` | The listing finished, with no error, and there were no members. Not an error: an empty tar is a real thing (`tar cf empty.tar --files-from /dev/null`), and it is byte-identical to a zero-filled junk file of the same size. |
| `EXTENSION_FORMAT_UNCONFIRMED` | The format came from the **filename**, nothing in the bytes confirmed it, and the listing came back empty. The classic shape is 32 KiB of zeros called `z.tar`. |
| `EXPLICIT_FORMAT_LISTED_EMPTY` | You passed `format=`, the listing came back empty, and detection disagrees. `format=` stays an override — wrong extensions are exactly what it is for — so this tells you rather than refusing. |
| `PASSWORD_ARGUMENT_UNUSED` | You passed `password=` to a format with no encryption. Passing a keyring across a batch of mixed archives is the intended use, so it is accepted and simply never consulted. |
| `ENCODING_ARGUMENT_UNUSED` | You passed `encoding=` to a backend that decodes names another way — 7z stores UTF-16, RAR decodes in its own parser, directory and single-file names come from the filesystem. |
| `MEMBER_NAME_BIDI_CONTROL` | A member name contains a Unicode bidi formatting control. The context names the exact codepoints, because an *override* (U+202A–202E, U+2066–2069 — how `evil‮gnp.exe` displays as a `.png`) is a different thing from a *directional mark* (U+061C, U+200E, U+200F), which appears in ordinary Arabic and Hebrew filenames. |

## When an archive is damaged

The rest of this page is the detail behind "we raise rather than quietly returning
wrong data". Most callers never need it; reach for it when you are recovering data
from archives you do not control.

### Listing a damaged archive

`members()` / `scan_members()` assert a **complete** listing (raise on terminal
archive damage). When you want the recoverable prefix *and* the error together, use
`members_report()`:

```python
with archivey.open_archive("messy.tar") as reader:
    report = reader.members_report()
    for member in report:                    # recovered members (may be a prefix)
        print(member.name)
    if report.error is not None:             # incomplete listing
        raise report.error
```

`__iter__` / `stream_members()` **yield the prefix then raise** on the same failures.
Diagnostics alone are not the primary signal. This is not salvage (resync past damage);
`--salvage` remains reserved. Random-access extract still fail-closes before writing
when listing ends in terminal damage.

### The integrity guarantee

**Read a member to its end and Archivey checks it.** Where the archive stores a
checksum or an authentication tag, a full read verifies it and raises if it does not
match. Stop early and nothing is checked. Errors always come from `read()`, never from
`close()` — so a `finally` block can't mask one.

"To its end" means `read(-1)`, reading until `read()` returns `b""`, or — for a member
with a declared size — reading that many bytes.

What that does and does not promise:

- **We try to raise on every error we can detect** — not on every error. Some formats
  store no checksum at all, and some damage decodes into something that looks
  perfectly valid.
- **`CorruptionError` vs `TruncatedError` is a best-effort guess, not a diagnosis.**
  Damage that happens to decode into a shorter stream is indistinguishable from a
  genuine truncation. Don't branch on which one you got — `except archivey.ReadError`
  catches both.
- **Bytes delivered before the error are of unknown quality.** When a compressed
  member fails mid-stream, some of what you already read is probably fine — but we
  can't tell you which part, or how much. Treat the prefix as unverified: not
  known-good, not known-bad.
- **A full-length return means the checksum matched.** Trust it as far as you trust
  that digest.
- **A short return with no exception does not mean "complete".** `read(member.size)`
  on a truncated member hands back what it has and stays quiet. Check the length — or
  just read again, because the *next* read raises.

That last point is what makes the ordinary chunked loop safe: it delivers every byte
that was readable and *then* raises, rather than ending quietly on a short member. So
the recoverable prefix and the error both reach you.

```python
buf = bytearray()
try:
    with reader.open("member.bin") as stream:
        while chunk := stream.read(1 << 20):
            buf.extend(chunk)
except archivey.ReadError:
    ...  # buf holds everything that was readable; the member is damaged
```

If you need certainty regardless of how you read — partial reads, seeks, or "never
hand me unverified bytes" — `VerificationMode.STRICT` verifies a whole member before
returning any of it.

#### What each call does

For a member whose declared size is 500 bytes, truncated after 110:

| Call | Corrupt at full length | Truncated after 110 of 500 |
| --- | --- | --- |
| `read(109)` | not yet at the end — no error | returns 109, no error |
| `read(110)` | not yet at the end — no error | returns 110, no error |
| `read(111)` | not yet at the end — no error | returns 110; the next `read()` raises |
| `read(member.size)` | raises `CorruptionError` | returns 110 short, **no exception** |
| `read(-1)` | raises `CorruptionError` | raises `TruncatedError` |
| chunked until `b""` | raises on the chunk that reaches the size, and withholds it | delivers the prefix, then raises |
| partial read, then `close()` | quiet — you stopped early | quiet — you stopped early |

The one row worth remembering is **`read(member.size)`**: it raises on corruption but
returns a short buffer on truncation. Known-wrong bytes are withheld; an apparently
incomplete prefix is handed over. So a short return from that call is a signal, not a
success — check the length.

Members with no declared size have nothing to read *to*, so `read(n)` can't
self-certify at all. Use `read(-1)` or read until `b""`.
