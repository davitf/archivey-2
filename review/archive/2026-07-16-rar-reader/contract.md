# Error contract, metadata fidelity, concurrency

This file covers brief items C (error contract & translation), D (metadata
fidelity / `-ver`), and E (concurrency & lifecycle) that are not the headline
findings. The two contract *bugs* (F1 wrong-password mislabel, F4 exit-code map)
live in `hostile-input.md` / `unrar-boundary.md`; this is the rest of the seam.

## C — error boundary: does the RAR reader re-implement S1/S3, or share them?

**It shares them** — no new copy of the tree-wide translate/stamp pattern
(old `deep-simplification.md` S1) or a hand-rolled pass loop (S3):

- Data-stream decode errors go through the shared `_wrap_member_stream` /
  `_translated_errors` boundary in `base_reader.py`; `RarReader` does not catch and
  re-stamp per site.
- The solid pass is driven by the shared `SolidBlockReader` primitive, not a bespoke
  loop, and defers to `super()._iter_with_data()` for the nonsolid case.

Raw-exception hygiene in the parser is good: `struct.error` is prevented by explicit
length checks before every `unpack_from`; `_require_exact` maps `OverflowError` to
`CorruptionError`; `_load_unixtime` / `_load_windowstime` swallow out-of-range
`ValueError`/`OverflowError`/`OSError`; `UnicodeDecodeError` is avoided by
`errors="replace"`/`"surrogateescape"` on every name decode. The RAR3 header-decrypt
path wraps everything in `try/except Exception → EncryptionError`
(`rar_parser.py:822-827`), and RAR5 likewise for `_rar5_decrypt_header`
(`1188-1195`).

**The one hole** is F1: the RAR5 wrap does *not* cover `_read_rar5_block` (called at
`1197`, outside the `try`), and the RAR3 post-decrypt block read + CRC check
(`830-883`) sit outside the decrypt wrap, so a wrong-password structural failure
escapes as `CorruptionError`. See `hostile-input.md` F1.

## D — metadata fidelity & the `-ver` feature (#107)

This is implemented carefully and I found **no bug** here:

- **Version members can't shadow the live file during a naive `extract_all`.**
  History rows are presented as `path;n` (`_presented_filename`), tagged
  `extra["rar.file_version"] = n`, and set `is_current=False`
  (`rar_reader.py:393-396,425`). The spec's `safe-extraction` coordinator skips
  `is_current=False` rows, so `extract_all` writes only the live `path`. The live
  revision keeps the plain name and `is_current=True`.
- **Solid demux stays aligned** — `_iter_with_data` passes `unrar -ver` exactly when
  any *payload* versioned FILE is present (`rar_reader.py:458-469`), matching the
  spec's "ALL-pipe demux uses `-ver`" requirement, and skips non-payload members in
  the size map (`is_payload_file()` gate at `482`).
- **The `;n` split is guarded.** `_rar3_split_file_version` only splits when the
  suffix after the last `;` is all digits (`rar_parser.py:1096-1099`), so a normal
  filename containing `;` (e.g. `a;b.txt`) is not misattributed — and it only runs at
  all when the RAR3 `FILE_VERSION` flag is set (`1042-1043`).

**Timestamp handling** is version-correct and does not duplicate a subtly-different
copy of `internal/timestamps.py`: RAR4 DOS time → naive `datetime`
(`_parse_dos_time`, with a graceful clamp for out-of-range fields), RAR5 → aware UTC
via `_load_unixtime` / `_load_windowstime`. The FILETIME epoch constant
(`116444736000000000`, `rar_parser.py:526`) is the standard 1601→1970 offset and is
duplicated from `internal/timestamps.py` — a *minor* DRY nit (a drift hazard if one
copy is ever corrected), not a correctness bug; the arithmetic matches. Sub-second
conversion clamps to `999999 µs` and swallows overflow, so hostile tick values can't
abort a listing.

One **cosmetic** edge (not a security issue): a header-encrypted RAR3/RAR5 archive's
`CMT` comment is read from the **raw** source at `data_offset`
(`rar_parser.py:937-939`, `1303-1305`) rather than through the header-decrypt stream,
so under whole-header encryption the "comment" would be decoded from ciphertext →
garbage text. Comments are best-effort and `errors="replace"` keeps it from raising,
so worst case is a nonsense comment string, never a crash. Flagging only for
awareness.

## E — concurrency & lifecycle

The backend honours the reader-concurrency contract:

- **Single-live-stream.** The solid pass closes each `previous` stream before opening
  the next (`rar_reader.py:476-500`) and `SolidBlockReader` enforces non-decreasing
  offsets with one active `_MemberSlice`. Random access uses `SharedSource.view`
  (per-view locked re-seek in `SlicingStream`), so concurrent direct reads of stored
  members don't clobber each other.
- **Materialization election / draining close** are inherited from `BaseArchiveReader`
  (`_get_members_registered`, `begin/complete/fail_materialization`), not
  re-implemented. `RarReader` only adds `_close_archive`, which terminates the live
  `unrar`, closes the shared source, the owned `ConcatenatedFile`, and removes any
  temp dir/file — each guarded so one failure doesn't skip the rest.
- **`BaseException` mid-stream** does not leak the subprocess: the `unrar` `Popen` is
  always inside a `_track_decompressed`-registered `_UnrarOwnedStream`, and
  `_materialize_stream_volumes` cleans its temp dir on `BaseException`
  (`rar_reader.py:270-273`). The old finding-#2 "handle not released on BaseException"
  shape does not recur.

**Minor:** after a successful nonsolid `_open_member`, `self._live_unrar` keeps
pointing at that member's process and is only reset to `None` on the exception path
(`rar_reader.py:584-587`) or in `_close_archive`. This is harmless — each process is
independently owned/terminated by its `_UnrarOwnedStream`, and `terminate_unrar`
polls before signalling, so the stale handle at most triggers one idempotent no-op
`terminate` at close. Not worth a fix beyond a comment, but noted since `_live_unrar`
reads like a single-owner field and isn't one.
