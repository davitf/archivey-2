# RAR Archive Support (via rarfile + system unrar)

## Purpose

The system reads RAR archives through the `rarfile` library combined with the system `unrar` binary. Because solid RAR archives trigger O(N × archive_size) decompression work under rarfile's default per-file subprocess model, the backend uses a one-shot `unrar x` extraction to a temporary directory to reduce solid-archive access to a single subprocess invocation.

## Requirements

### Requirement: Declare format properties

The system SHALL expose the following properties for the RAR backend:

| Property | Value |
|----------|-------|
| Backend dependency | `rarfile` ≥ 4.0 (requires `unrar` binary on PATH) |
| Listing cost | O(1) — central directory parsed upfront |
| Access cost | SOLID if solid archive; DIRECT otherwise |
| Supports write | No — RAR is proprietary; read-only |
| Requires seek | Yes |

#### Scenario: write attempt on a RAR archive

- **WHEN** a caller attempts to create or write a RAR archive
- **THEN** the system SHALL raise `UnsupportedOperationError`, because RAR write is not supported

#### Scenario: opening from a non-seekable source

- **WHEN** the source stream does not support seeking
- **THEN** the backend SHALL reject the open with an appropriate error, because `Requires seek` is `True`

---

### Requirement: Use rarfile pull model for non-solid archives

The system SHALL use `rarfile.RarFile.open(name)`, which returns a `RarExtFile` (`RawIOBase`), as the pull-based stream for non-solid archives. For non-solid archives, rarfile uses an internal hack: it extracts just the target member's compressed bytes into a temporary mini-archive and runs `unrar` on that subset. This is O(member_size) per call.

#### Scenario: opening a member from a non-solid RAR

- **WHEN** `_open_member()` is called on a non-solid RAR archive
- **THEN** the system SHALL call `rarfile.open(member.original_name)` and return the resulting `RarExtFile` stream directly

---

### Requirement: Detect and handle solid-archive limitation

The system SHALL detect solid archives on open. For solid RAR archives, rarfile's default `extractall()` does NOT batch-extract — it calls `open()` once per file internally, spawning a separate `unrar` subprocess for each. Every subprocess re-processes the full archive from the start. Iterating N members of a solid RAR this way results in O(N) subprocess invocations each doing O(archive_size) work, totalling O(N × archive_size) decompression work. This behaviour is prohibited.

#### Scenario: naive per-file extraction on a solid RAR

- **WHEN** a solid RAR archive contains N members and `_open_member()` is called once per member without the one-shot workaround
- **THEN** each call spawns a separate `unrar` subprocess that re-processes the entire archive — this O(N × archive_size) behaviour SHALL NOT occur

---

### Requirement: Apply one-shot unrar extraction for solid archives

The system SHALL detect solid archives on open and, for `_open_member()`, run `unrar x` exactly once to extract all files to a `TemporaryDirectory`, then serve subsequent `_open_member()` calls by opening the pre-extracted files from that directory. The temporary directory persists until `close()`.

```python
def _open_member(self, member: Member) -> BinaryIO:
    if not self._is_solid:
        return self._rf.open(member.original_name)   # rarfile's hack, efficient
    self._ensure_solid_cache()                        # runs unrar x once, lazy
    return open(self._solid_cache_dir / member.name, 'rb')

def _ensure_solid_cache(self):
    if self._solid_cache_dir is not None:
        return
    self._solid_cache_dir = Path(tempfile.mkdtemp())
    setup = rarfile.tool_setup()
    cmd = [setup._unrar_tool, 'x', '-inul',
           f'-p{self._password}' if self._password else '-p-',
           str(self._path), str(self._solid_cache_dir) + os.sep]
    subprocess.run(cmd, check=True)
```

`unrar x` (not `e`) is used to preserve relative paths and avoid name collisions. The command is built from rarfile's `tool_setup()` to respect any user-configured tool path. Disk space required equals the uncompressed size of the archive; the temporary directory is cleaned up in `close()`.

#### Scenario: first member access on a solid RAR

- **WHEN** `_open_member()` is called for the first time on a solid RAR archive
- **THEN** the system SHALL invoke `unrar x -inul <archive> <tmpdir>/` exactly once, extracting all files to a temporary directory
- **AND** subsequent `_open_member()` calls SHALL open pre-extracted files from that directory without spawning additional subprocesses

#### Scenario: cleanup of solid cache

- **WHEN** `close()` is called on a solid RAR reader
- **THEN** the temporary directory created by the one-shot extraction SHALL be removed

---

### Requirement: Provide bounded-memory streaming path via _iter_with_data()

The system SHALL override `_iter_with_data()` for solid RAR archives to also run `unrar x` exactly once, but clean up the temporary directory as soon as the `stream_members()` iteration ends (via a `finally` block in the generator) rather than at `close()`. Both `_open_member()` and `_iter_with_data()` run `unrar` exactly once; the distinction is the disk lifetime of the extracted files.

```python
def _iter_with_data(self) -> Iterator[tuple[Member, BinaryIO]]:
    if not self._is_solid:
        yield from super()._iter_with_data()   # default: open() per member
        return
    tmpdir = Path(tempfile.mkdtemp())
    try:
        setup = rarfile.tool_setup()
        subprocess.run([setup._unrar_tool, 'x', '-inul', ...], check=True)
        for member in self._iter_members():
            yield member, open(tmpdir / member.name, 'rb')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
```

#### Scenario: streaming a solid RAR with stream_members()

- **WHEN** `stream_members()` is called on a solid RAR archive
- **THEN** the system SHALL run `unrar x` exactly once into a temporary directory, yield all members with file handles into that directory, and remove the directory when the iteration is complete (via the `finally` block)

#### Scenario: disk lifetime difference between _open_member and _iter_with_data

- **WHEN** members are accessed via `ar.open(member)` on a solid RAR
- **THEN** the extracted files remain on disk until `close()` is called
- **WHEN** members are accessed via `stream_members()` on a solid RAR
- **THEN** the extracted files are removed as soon as the iteration loop finishes, not waiting for `close()`

---

### Requirement: Report the absence of solid block boundary information

The system SHALL acknowledge that rarfile does not expose which files belong to the same solid compression block. The solid/non-solid distinction for RAR is binary per archive — there is no per-block granularity available. `CostReceipt.solid_block_count` SHALL be `None` for RAR archives.

#### Scenario: CostReceipt for a solid RAR

- **WHEN** a solid RAR archive is opened
- **THEN** `CostReceipt.is_solid` SHALL be `True` and `CostReceipt.solid_block_count` SHALL be `None`, because rarfile does not expose block boundary information

---

### Requirement: Handle RAR4 and RAR5 timestamp differences

The system SHALL map timestamps differently depending on the RAR version:

- **RAR4:** stores local wall-clock time → `Member.modified` is a naive `datetime` (no timezone).
- **RAR5:** stores UTC with sub-second precision → `Member.modified` is a timezone-aware `datetime`.

#### Scenario: RAR4 archive timestamp

- **WHEN** a RAR4 archive is opened and a member's modification time is read
- **THEN** `Member.modified` SHALL be a naive `datetime` representing local wall-clock time

#### Scenario: RAR5 archive timestamp

- **WHEN** a RAR5 archive is opened and a member's modification time is read
- **THEN** `Member.modified` SHALL be a timezone-aware UTC `datetime`

---

### Requirement: Handle RAR5 link types correctly

The system SHALL handle RAR5 link semantics as follows:

- **Hardlinks and file-copies:** RAR5 stores these via the `file_redir` field. `rarfile` automatically follows `RAR5_XREDIR_HARD_LINK` and `RAR5_XREDIR_FILE_COPY` redirects inside `open()`, transparently returning the source file's data. No additional handling is required at the backend layer for these.
- **Symlinks:** RAR5 stores symlinks via `RAR5_XREDIR_UNIX_SYMLINK`, with the link target path as the member's content. The ABC-level link-following (defined in the `ArchiveReader` base class) handles symlinks uniformly across all formats.

#### Scenario: opening a RAR5 hardlink or file-copy member

- **WHEN** `open()` is called on a member that is a RAR5 hardlink (`RAR5_XREDIR_HARD_LINK`) or file-copy (`RAR5_XREDIR_FILE_COPY`)
- **THEN** `rarfile` automatically returns the source file's data, and no additional redirection is needed at the backend layer

#### Scenario: opening a RAR5 symlink member

- **WHEN** `open()` is called on a member that is a RAR5 Unix symlink (`RAR5_XREDIR_UNIX_SYMLINK`)
- **THEN** the ABC-level link-following layer resolves the target and redirects to the target member's data

---

### Requirement: Require a password to list a RAR5 header-encrypted archive

The system SHALL set `ArchiveInfo.is_encrypted = True` when a RAR5 archive uses header encryption. Listing members of such an archive requires a password; without one, the listing operation fails.

#### Scenario: listing a header-encrypted RAR5 archive without a password

- **WHEN** a RAR5 archive with header encryption is opened without supplying a password
- **THEN** the system SHALL raise `EncryptionError`, because listing requires decrypting the header

#### Scenario: ArchiveInfo for a header-encrypted RAR5 archive

- **WHEN** a RAR5 archive with header encryption is opened (with a valid password)
- **THEN** `ArchiveInfo.is_encrypted` SHALL be `True`
