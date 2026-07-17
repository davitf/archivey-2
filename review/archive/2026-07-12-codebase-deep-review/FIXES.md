# Fixes applied

Two rounds. Round 1 was the doc-only fixes from the initial review pass. Round 2 implemented the
changes the maintainer approved on PR #73 (each with a red-green test where it changes behavior).
All three dependency configs (`[all]`, `[all-lowest]`, `core-only`) and both type checkers stay
green.

## Round 1 — doc-only (initial review)

| # | File | What | Commit |
|---|------|------|--------|
| 1 | `core.py` | `open_stream` docstring: "return the decompressed bytes" → "return a decompressing stream" (it returns an `ArchiveStream`). | "Fix open_stream docstring: it returns a stream, not bytes" |
| 2 | `reader.py` | `ArchiveReader.open` docstring: foreign-member open raises `ValueError` → `ArchiveyUsageError` (confirmed by `test_reader_contract.py`). | "Fix ArchiveReader.open docstring: identity mismatch raises ArchiveyUsageError" |

## Round 2 — maintainer-approved implementations (PR #73)

| # | Review ref | File(s) | What | Test |
|---|-----------|---------|------|------|
| Q1 | C1 / L2 | `base_reader.py` | Materialization state-cleanup now `except BaseException` (re-raising), so a KeyboardInterrupt/MemoryError mid-scan can't wedge the reader (stuck `MATERIALIZING` → misleading error / CV deadlock). Inline comment guards the choice. | `test_baseexception_during_materialization_does_not_wedge_reader` (red-green) |
| Q2 | C2/C3 / S2/S3 | `directory_reader.py`, `cost.py`, `format-directory/spec.md`, tests | Directory reports `ListingCost.REQUIRES_SCANNING` and `_MEMBER_LIST_UPFRONT=False` (a walk is a scan). `get_members_if_available()` now returns `None` before a pass (no uncached walk, no free-threaded cache race). Spec + `INDEXED` docstring updated. | `test_get_members_if_available_returns_none_before_scan`, updated cost tests |
| Q3 | S1 / E1 | `zip_reader.py`, tests | ZIP surfaces the central-directory CRC-32 as `member.hashes["crc32"]` (FILE/SYMLINK only), enabling cheap dedupe. zipfile still runs its own CRC check (no VerifyingStream). | `test_file_member_exposes_stored_crc32`, `test_directory_member_has_no_crc32` |
| Q4 | L1 / O1 | `sevenzip_parser.py`, tests | Bound the 7z file count against the header size before pre-allocating, closing an OOM-on-hostile-input DoS (a 5-byte field could request 2⁴⁰ allocations). Answer to the maintainer's max_entries question: see reply below. | `test_files_info_count_is_bounded_against_header_size` (without it the test hangs/OOMs) |
| D1 | latent-bugs D1 | `sevenzip_reader.py` | Bind `lzma._decode_filter_properties` once at import and raise `ImportError` if absent, instead of catching `AttributeError` per call and mislabeling every LZMA member as corrupt. (py7zr uses the same private function — no public replacement exists.) | covered by existing 7z reader tests |
| D2 | latent-bugs D2 | `open_site.py` | Capture only `file:line`; drop the unconditional `extract_stack()` + retained full-stack tuple that nothing reads. `extract_stack` now only runs on the rare no-`_getframe` fallback. | existing concurrency tests |
| X1 | complexity X1 | `base_reader.py` + 3 backends | `BaseArchiveReader._handle_guard()` collapses the copy-pasted `if lock: with lock: … else: …` shared-handle branch (~12 sites). No behavior change (nullcontext no-op). | existing backend + concurrency tests |
| X2 | complexity X2 | `internal/timestamps.py` (new) + zip/7z | Shared NTFS FILETIME conversion + `TimestampIssue` (was duplicated in ZIP and 7z; RAR can reuse). | existing timestamp tests |
| X3 | complexity X3 | `zip_reader.py` | `_ZIP_MEMBER_READ_ERRORS` constant + `_reraise_member_error` helper; the three catch sites no longer drift (the symlink-target read had already dropped `io.UnsupportedOperation` — now consistent). | existing ZIP/error tests |
| X4 | complexity X4 | `zip_reader.py` | Documented the four phases of the STORED ZipCrypto password path (docstring + section markers); no control-flow change. | existing multipassword tests |
| X5 | complexity X5 | `base_reader.py` | Replaced the no-op `if previous is not None: pass` tail with a plain comment. | — |
| O-1 | other O-1 | `iso_reader.py`, `docs/internal/known-issues.md` | Documented that importing the ISO backend patches pycdlib process-globally (the cycle guard). | — |
| Q5 | other O-4 | — | No change needed: the strict-EOF-vs-IGNORE-policy precedence is already specced (`format-tar/spec.md:151`) and tested (`test_strict_eof_ignore_still_raises_truncated`). My "not specced" framing was wrong. | already covered |

## Reply to the maintainer's open questions (PR comments)

- **Q4 — use `ExtractionLimits.max_entries` as the ceiling?** I chose the header-size bound instead,
  for two reasons: (1) `max_entries` is an *extraction* limit (default 2²⁰) and the parser runs at
  *open/listing* time, so reusing it conflates two phases and would need config plumbed into the
  otherwise-pure parser; (2) the header-size bound *scales* — a legitimate million-file archive has a
  proportionally large header, so it's never falsely rejected, while `max_entries=2²⁰` would reject a
  legitimate 2M-file archive at listing. A future dedicated **listing-limits** config (roadmap) is the
  right home for an explicit member cap; I noted it there.
- **single-file hashes (roadmap:63):** checked — single-file readers surface decompressed *size*
  (xz/lzip) but **no stored digest**. gzip and lzip both carry a CRC-32 of the decompressed content
  in their trailer, cheaply readable from a seekable/path source — genuinely useful for dedupe. It
  needs its own small design (extend `MetadataContext` with a trailer peek; handle multi-member gzip,
  where the trailer CRC covers only the last member). Left as a focused follow-up rather than bolting a
  half-considered trailer reader into this PR; added to `IDEAS.md` / roadmap review.
- **ArchiveMember hashability (other O-3):** discussed in `review/other.md` — recommend keeping it
  unhashable; see the reply there.
- **free-threading with external backends (roadmap:67):** discussed in `review/roadmap.md`.
