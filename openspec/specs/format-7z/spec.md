# 7-Zip Archive Support (py7zr)

## Purpose

Archivey reads and writes 7-Zip archives using the `py7zr` library (optional `[7z]` extra). Because py7zr exposes only a push-based extraction API with no streaming pull, the backend uses lazy per-folder caching to avoid the O(N²) decompression hazard that solid archives would otherwise cause under naive per-file extraction.

## Requirements

### Requirement: Declare format properties

The system SHALL expose the following properties for the 7-Zip backend:

| Property | Value |
|----------|-------|
| Backend dependency | `py7zr` ≥ 0.20 |
| Listing cost | O(1) — header parsed upfront via `sz.list()` |
| Access cost | SOLID (typically); DIRECT only if no solid blocks |
| Supports write | Yes (via `py7zr`) |
| Requires seek | Yes |

#### Scenario: listing members of a 7z archive

- **WHEN** a caller opens a 7-Zip archive
- **THEN** the backend reads the header block (fast, at start of file) and the full member list is available in O(1) without decompressing any file data

#### Scenario: opening a 7z archive from a non-seekable source

- **WHEN** the source stream does not support seeking
- **THEN** the backend rejects the open with an appropriate error, because `Requires seek` is `True`

---

### Requirement: Handle py7zr push model correctly

The system SHALL accommodate py7zr's push-based extraction model, which provides no `open(name) -> stream` pull API. The only extraction interfaces are `extract(targets=[...], factory=WriterFactory)` and `extractall(factory=WriterFactory)`, which push bytes into `Py7zIO` objects. `reset()` must be called between successive extraction calls. The `Py7zIO` objects supplied to py7zr must be seekable — py7zr calls `seek(0)` after each file as a final rewind (it never reads back), so `BytesIO` and `SpooledTemporaryFile` work; pipes fail.

#### Scenario: extracting a single member using the push model

- **WHEN** the backend needs to open a member for reading
- **THEN** it uses `sz.extract(targets=[...], factory=WriterFactory)` to push data into a seekable buffer, then calls `sz.reset()` before the next extraction

#### Scenario: providing a non-seekable Py7zIO object

- **WHEN** a non-seekable object (e.g. a pipe) is supplied as the write target
- **THEN** py7zr fails because it calls `seek(0)` after writing, so the backend must only supply seekable buffers (`BytesIO`, `SpooledTemporaryFile`)

---

### Requirement: Avoid the solid-archive O(N²) decompression hazard

The system SHALL NOT call `_open_member()` naively once per file when members share a solid block. Calling `extract(targets=['c.txt'])` on a solid block `[a, b, c]` still decompresses `a` and `b` — `targets` controls data capture, not decompression work. A naive per-file loop across a solid block of N files would trigger O(N) decompression passes per file, yielding O(N²) total decompression work.

#### Scenario: accessing multiple files in the same solid block naively

- **WHEN** N files share a solid compression block and `_open_member()` is called once per file without caching
- **THEN** each call decompresses the entire block from the start, causing O(N²) total decompression work — this behaviour is prohibited

---

### Requirement: Apply lazy per-folder caching in `_open_member()`

The system SHALL implement lazy per-folder caching in `_open_member()`: the first time any member from a given solid block (folder) is requested, the backend extracts the entire folder in one pass and caches all members from it. Subsequent requests for members in the same folder are served from the cache in O(1). Cache entries are never evicted; memory grows until `close()`. The `SpooledTemporaryFile` threshold is 64 MiB — buffers up to that size stay in memory; larger ones spill to disk.

The caching strategy gives O(1) decompression passes per solid block regardless of access pattern. For sequential `for member in ar: ar.open(member)`, total decompression cost is O(number_of_solid_blocks), not O(N). Memory peak equals the size of the largest single solid block uncompressed.

Example access pattern:

```
first open(a):  → extract_folder(folder_0)  [decompresses a, b, c]  → cache all three
     open(b):  → cache hit                   [O(1), from SpooledTemporaryFile]
     open(c):  → cache hit                   [O(1)]
first open(d):  → extract_folder(folder_1)  [decompresses d, e]
```

Folder-to-file mapping is available from py7zr internals: each `FileInfo` dict has a `"folder"` key pointing to its `Folder` object; files in the same solid block share the same `Folder` instance (object identity). `SubstreamsInfo.num_unpackstreams_folders[i]` gives the file count per folder.

The implementation of `_open_member()`:

```python
def _open_member(self, member: Member) -> BinaryIO:
    name = member.original_name
    folder = self._file_info_map[name]["folder"]

    if folder not in self._folder_cache:
        targets = [fi["filename"] for fi in folder.files]
        class BlockFactory(py7zr.WriterFactory):
            def create(inner_self, fn):
                spooled = tempfile.SpooledTemporaryFile(max_size=64 << 20)
                self._folder_cache[folder][fn] = spooled   # written into permanent cache
                return _Py7zIOAdapter(spooled)
        self._folder_cache[folder] = {}
        self._sz.extract(targets=targets, factory=BlockFactory())
        self._sz.reset()   # required before next extraction

    buf = self._folder_cache[folder][name]
    buf.seek(0)
    return buf
```

#### Scenario: first access to a member in a previously unseen folder

- **WHEN** `_open_member()` is called for a member whose folder is not yet in `_folder_cache`
- **THEN** the backend extracts all files in that folder in one `sz.extract()` call, caches every resulting `SpooledTemporaryFile` in `_folder_cache[folder]`, calls `sz.reset()`, and returns the requested buffer seeked to position 0

#### Scenario: subsequent access to a member in a cached folder

- **WHEN** `_open_member()` is called for a member whose folder is already in `_folder_cache`
- **THEN** the backend retrieves the buffer from the cache, seeks it to position 0, and returns it — no decompression occurs

#### Scenario: folder buffer size above 64 MiB

- **WHEN** a folder's uncompressed data for a single file exceeds 64 MiB
- **THEN** the `SpooledTemporaryFile` spills to a temporary file on disk rather than holding the data in memory

---

### Requirement: Provide bounded-memory sequential iteration via `_iter_with_data()`

The system SHALL override `_iter_with_data()` to iterate folders one at a time, extracting each folder in a single pass, yielding all its members with their streams, and then releasing that folder's `SpooledTemporaryFile`s before moving to the next folder. This path is used by `stream_members()` and gives bounded memory: peak equals the size of the largest single solid folder.

```python
def _iter_with_data(self) -> Iterator[tuple[Member, BinaryIO]]:
    for folder in self._folders:
        targets = [fi["filename"] for fi in folder.files]
        folder_bufs: dict[str, SpooledTemporaryFile] = {}

        class FolderFactory(py7zr.WriterFactory):
            def create(inner_self, fn):
                spooled = tempfile.SpooledTemporaryFile(max_size=64 << 20)
                folder_bufs[fn] = spooled
                return _Py7zIOAdapter(spooled)

        self._sz.extract(targets=targets, factory=FolderFactory())
        self._sz.reset()

        for fi in folder.files:
            member = self._member_map[fi["filename"]]
            buf = folder_bufs[fi["filename"]]
            buf.seek(0)
            yield member, buf

        # folder_bufs goes out of scope here → GC releases SpooledTemporaryFiles
        # Peak memory = size of the largest single folder
```

`_iter_members()` is pure metadata: it calls `sz.list()`, is O(1), and performs no decompression.

#### Scenario: streaming all members with `stream_members()`

- **WHEN** a caller uses `stream_members()` to iterate a solid 7-Zip archive
- **THEN** the backend extracts one folder at a time, yields all members in that folder, then releases the folder's buffers before decompressing the next folder
- **AND** peak memory at any point equals the uncompressed size of the largest single solid folder

#### Scenario: comparing memory profiles of `for m in ar: ar.open(m)` vs `stream_members()`

- **WHEN** a caller iterates using `for m in ar: ar.open(m)` on a solid archive
- **THEN** each accessed folder's cache accumulates until `close()`, so peak memory equals the sum of all solid blocks accessed
- **WHEN** a caller iterates using `stream_members()`
- **THEN** peak memory equals the size of the largest single solid block, because buffers are released folder by folder

---

### Requirement: Report solid block metadata in CostReceipt

The system SHALL populate `CostReceipt` fields for 7-Zip archives as follows: `solid_block_count` is sourced from `archiveinfo().blocks`; `is_solid` is sourced from `archiveinfo().solid`.

#### Scenario: reporting cost for a solid 7z archive

- **WHEN** a 7-Zip archive contains multiple solid blocks
- **THEN** `CostReceipt.is_solid` is `True` and `CostReceipt.solid_block_count` reflects the actual number of solid blocks as reported by `archiveinfo().blocks`

#### Scenario: reporting cost for a non-solid 7z archive

- **WHEN** a 7-Zip archive has no solid blocks (each file is independently compressed)
- **THEN** `CostReceipt.is_solid` is `False` and `CostReceipt.access_cost` is `DIRECT`

---

### Requirement: Map compression chain to CompressionMethod

The system SHALL map 7-Zip codec information to `CompressionMethod` values. Archive-level method names are available from `archiveinfo().method_names` (e.g. `['LZMA2', 'BCJ']`); per-folder codec details are available from `Folder.coders`. These are mapped to `CompressionAlgo` values and stored as a `tuple[CompressionMethod, ...]` on each `Member`, modelling the filter chain (e.g. `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))` for a typical 7z executable entry).

#### Scenario: member compressed with a BCJ + LZMA2 filter chain

- **WHEN** a member in a 7-Zip archive uses a BCJ pre-filter followed by LZMA2 compression
- **THEN** `member.compression` is `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))` (or the applicable BCJ variant), reflecting the full filter chain in order

---

### Requirement: Represent absent POSIX metadata as None

The system SHALL set `mode`, `uid`, and `gid` to `None` when the 7-Zip archive does not include a POSIX metadata attribute block. 7z stores POSIX metadata in an optional attribute block; if it is absent, these fields must be `None` — not a guessed default.

#### Scenario: 7z archive created on Windows without POSIX attribute block

- **WHEN** a 7-Zip archive lacks a POSIX metadata attribute block
- **THEN** `member.mode`, `member.uid`, and `member.gid` are all `None` for every member
