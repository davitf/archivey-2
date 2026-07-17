# Layer map — per-backend member-stream stacks (post-#136)

Measurement OFF. Stacks are **bottom → public**. Line refs are on PR #136
(`2a6b91b`). `#136` collapses direct nested `ArchiveStream`s inside
`_ensure_open`; it does **not** look through `VerifyingStream`, so a codec
`ArchiveStream` under verify remains (see ZIP/STORED).

Shared rules:

- Public wrap: `BaseArchiveReader._wrap_member_stream` —
  `base_reader.py:573-650`
- Lazy `stream_members`: `_lazy_member_stream` — `base_reader.py:461-479`;
  collapse — `archive_stream.py:217-223`, `:238-293`
- `_track_decompressed` / seek wrappers are identity when measurement is off —
  `base_reader.py:526-543`
- `open_codec_stream` may apply `fix_stream_start_position` then returns a
  codec `ArchiveStream` — `codecs.py:1441-1471`

---

## ZIP

### Unencrypted STORED

Composition: `_raw_member_stream` `zip_reader.py:911-928` →
`open_codec_stream(STORED)` `:1012-1024` → `VerifyingStream` `:1030-1040` →
`_wrap_member_stream` `:1041`.

```
ZipFile.fp
  SlicingStream(start=data_start, length=compress_size, lock=ZipFile._lock)
    ArchiveStream(codec)          # StoredCodec returns the slice as-is
      VerifyingStream(hashes, expected_size)
        ArchiveStream(member)     # public; stream_members collapses only this outer
```

Verify: **yes**. Slice: member-boundary, locked re-seek.

### Unencrypted deflate (and other codecs)

Same skeleton; codec layer is `DecompressorStream` / accelerator instead of
identity:

```
ZipFile.fp
  SlicingStream(locked member payload)
    DecompressorStream | _AcceleratorStream | …
      ArchiveStream(codec)
        VerifyingStream
          ArchiveStream(member)
```

### ZipCrypto (stdlib path)

`_open_member` encrypted branch `zip_reader.py:1319-1322` →
`ZipFile.open` `:1069-1074` → optional `CloseLockedStream` under CONCURRENT
`:1051-1055` → `_wrap_member_stream`.

```
ZipExtFile (zipfile owns decrypt/decompress/CRC)
  [CloseLockedStream]             # CONCURRENT only
    ArchiveStream(member)
```

Verify: **skipped** (stdlib CRC). No archivey `SlicingStream` on the returned
handle.

### WinZip AES (method 99)

`_open_aes_member` `zip_reader.py:752-825`:

```
ZipFile.fp
  SlicingStream(locked full AES payload)
    WinZipAesDecryptStream
      codec stream + ArchiveStream(codec)
        VerifyingStream            # trailing probe drains HMAC
          ArchiveStream(member)
```

---

## TAR

### Plain / random open

`tar_reader.py:492-511`:

```
tarfile.ExFileObject
  [LockedStream]                  # CONCURRENT or streaming handle lock
    ArchiveStream(member)
```

Verify: **no**. Slice: **no**.

### Compressed `.tar.gz` / `.tar.xz` / …

Outer codec once at archive open (`tar_reader.py:198-223`), then same member
stack. Streaming `_iter_with_data` (`:317-337`) always locks.

---

## ISO

`iso_reader.py:468-485`:

```
pycdlib.PyCdlibIO
  _PyCdlibStream
    [LockedStream]                # CONCURRENT
      ArchiveStream(member)
```

Verify: **no**. Slice: **no**. `SharedSource`: **no**.

---

## 7z

### Folder pipeline (solid and non-solid)

`SharedSource` at open (`sevenzip_reader.py:189`) → pack
`SharedSource.view` (`:465-477`) → pipeline stages
(`sevenzip_pipeline.py:305-368`: AES / codec+`ArchiveStream` / optional LZMA
cap `SlicingStream` / BCJ).

### Random `open(member)`

`sevenzip_reader.py:657-691` + verify wrap `:600-613`:

```
decoded folder stream
  SlicingStream(prefix, length=size, own_source=True)   # or forward skip + length slice
    VerifyingStream                 # if size or hashes
      ArchiveStream(member)
```

### `stream_members` solid lazy (#136)

`sevenzip_reader.py:615-639` + `solid.py:136-145`:

```
decoded folder stream
  SolidBlockReader
    _MemberSlice(pending)           # positions on first read
      VerifyingStream               # inside ArchiveStream open_fn
        ArchiveStream(member, seekable=False)
```

Verify inside `open_fn` so `VerifyingStream.close` cannot probe an unselected
member into a solid open.

---

## RAR

### Direct stored

`SharedSource.view` → verify → wrap (`rar_reader.py:718-730`, `:670-716`):

```
archive source
  SlicingStream via SharedSource.view(data_offset, file_size)
    VerifyingStream
      ArchiveStream(member)
```

### `unrar p` (random / non-solid)

```
unrar stdout
  _UnrarOwnedStream
    VerifyingStream                 # usual case
      ArchiveStream(member)
```

### Solid lazy `stream_members` (#136)

`rar_reader.py:564-623`:

```
unrar ALL-pipe
  _UnrarOwnedStream
    SolidBlockReader
      _MemberSlice(pending)
        VerifyingStream             # inside open_fn
          ArchiveStream(member, seekable=False)
```

---

## Single-file compressed

`single_file_reader.py:304-359`:

| Source | Stack |
|--------|--------|
| Path (seekable) | codec stream → `ArchiveStream(codec)` → public `ArchiveStream` (**collapsed** on open) |
| Seekable `BinaryIO` | `SharedSource.view(0)` → codec → codec AS → public AS (collapsed) |
| Non-seekable | `[CountingReader?]` → codec → codec AS → public AS (collapsed) |

Verify: **skipped** at reader (codec-internal checks remain).
`fix_stream_start_position` may add a slice inside `open_codec_stream`.

---

## Directory

`directory_reader.py:269-276`:

```
open(path, "rb")
  ArchiveStream(member)
```

Verify / slice / locks: **none**.

---

## Cross-cutting

| Mechanism | Where used on hot path |
|-----------|------------------------|
| `VerifyingStream` | ZIP unencrypted + AES; 7z members; RAR payloads |
| Member-boundary `SlicingStream` | ZIP raw payload; 7z member over folder; RAR direct stored |
| `SharedSource` views | 7z, RAR, single-file seekable streams |
| `LockedStream` | TAR, ISO (CONCURRENT / streaming) |
| `CloseLockedStream` | ZIP ZipCrypto CONCURRENT close |
| `fix_stream_start_position` | `open_codec_stream`, nested `open_archive` |
| Nested codec `ArchiveStream` under verify | ZIP unencrypted/AES; remains after #136 |
| Decode engine | out of scope (Topic 6) |
