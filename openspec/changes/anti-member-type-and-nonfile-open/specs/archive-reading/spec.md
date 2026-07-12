## MODIFIED Requirements

### Requirement: Reading member data

`ArchiveStream` SHALL implement `BinaryIO`, remain caller-closed, and expose an
immutable operation-filtered diagnostic snapshot:

```python
class ArchiveStream(BinaryIO):
    @property
    def diagnostics(self) -> DiagnosticSummary: ...

def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Unknown name → `KeyError`; foreign `ArchiveMember` → `ValueError`. `read()`
materializes the full payload without extraction bomb checks (small trusted
members). `open()` streams in bounded chunks. Full reads verify supported digests;
streaming verification raises `CorruptionError` only on the terminal read after
valid chunks; `read()` raises without returning bytes.

After symlink/hardlink following, if the **resolved** member is not
`MemberType.FILE`, `open()` / `read()` SHALL raise `ArchiveyUsageError`. They MUST
NOT return empty bytes for directories, `ANTI`, or `OTHER`, and MUST NOT leak raw
`IsADirectoryError` or format `CorruptionError` for directory paths.

**Diagnostics (observable):** A reader-owned stream's `diagnostics` shows only
that open operation's events; the same events also appear on the reader's
cumulative snapshot without being retained twice. A standalone `ArchiveStream`
(not owned by a reader) has its own lifetime summary. Retention/budget rules:
`diagnostics`.

#### Scenario: read / open matrix

| Case | Expected |
| --- | --- |
| `open("data.bin")` succeeds | `ArchiveStream` as `BinaryIO`; `stream.diagnostics` = that operation only |
| Reader-owned stream emits rewind diagnostic | Visible on stream and reader snapshots; retained once |
| `read("readme.txt")` | Full uncompressed `bytes` |
| `open(member)` from a different reader | `ValueError` |
| `open`/`read` directory (ZIP/TAR/ISO/directory/7z) | `ArchiveyUsageError` |
| `open`/`read` `MemberType.ANTI` or `OTHER` | `ArchiveyUsageError` |
| Symlink resolves to a file | Follow succeeds; returns file stream/bytes |

## ADDED Requirements

### Requirement: Non-file stream_members yield None

`stream_members` SHALL pair every non-file member (`DIRECTORY`, `SYMLINK`,
`HARDLINK`, `OTHER`, `ANTI`) with `stream is None` (no empty `ArchiveStream`).

#### Scenario: non-file stream matrix

| Case | Expected |
| --- | --- |
| Directory member | Stream `None` |
| `MemberType.ANTI` | Stream `None` |
