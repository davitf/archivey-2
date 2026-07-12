# Theme 7 — Latent bugs & tech debt (prioritized)

Ranked by severity × likelihood. Cross-references to the other reports where a finding was first
raised. VERIFIED = I traced it to the failing path; SUSPECTED = needs a repro to confirm.

## L1 — Native 7z parser: unbounded `num_files` allocation → OOM on hostile input (VERIFIED, high)

`sevenzip_parser.py:571-572`:
```python
num_files = _read_uint64(buffer)
files = [_FileProps() for _ in range(num_files)]
```
`num_files` comes straight from the header with no bound. `_FileProps()` reads nothing from the
buffer, so the list comprehension pre-allocates `num_files` objects *before* any truncation check
can fire. A crafted archive (attacker controls the header bytes **and** the `next_header_crc` that
"validates" them — see unknown-unknowns U1) with `num_files = 2**40` triggers an unbounded
allocation → MemoryError / OOM-kill at `open_archive()` time (parsing runs in
`SevenZipReader.__init__`).

Contrast: pack-stream count *is* bounded (`_MAX_NUM_STREAMS`, `sevenzip_parser.py:384`); the
folder count is self-limiting (each `_read_folder` consumes buffer bytes); but `num_files`,
`num_empty_streams`, and the timestamp/attribute counts driven off it are not.

- **Failure scenario:** `open_archive("evil.7z")` where the 7z next-header declares `num_files =
  2**40` with a matching CRC → process OOM before the call returns.
- **Why the fuzzers miss it:** the mutation harness bit-flips *valid* archives, which invalidates
  the CRC and gets rejected at the CRC check before reaching `_read_files_info`. This needs a
  *crafted* header with a recomputed CRC — outside the mutation harness's reach and exactly what
  the (not-yet-stood-up) Atheris gate + a hand-built adversarial fixture would catch.
- **Impact on the pitch:** VISION claim #2 is "memory-safe parsing of hostile input." This is a
  pure-Python resource-exhaustion DoS, not memory corruption — but it still lets a crafted archive
  take down the process, which undercuts the claim in practice.
- **Fix direction (not applied — hostile-input parser, needs the right bound):** bound `num_files`
  against a sane cap and/or the remaining header size (every real file needs at least one bit in the
  empty-stream vector and a name, so `num_files` can't exceed a small multiple of `next_header_size`).
  This is threat-model O1 made concrete. See QUESTIONS Q4.

## L2 — BaseException during materialization wedges the reader (VERIFIED, medium)

Full analysis in concurrency.md C1. `base_reader.py:503` catches `except Exception`, so a
`KeyboardInterrupt`/`MemoryError`/`SystemExit` during `_iter_members()` leaves `cache_state =
MATERIALIZING` forever: subsequent non-concurrent calls raise a misleading "another materialization
in progress", and CONCURRENT waiters block on the CV indefinitely. Fix is to broaden the
state-cleanup handler to `BaseException` (re-raising). Not applied — concurrency mechanism. QUESTIONS Q1.

## L3 — ZIP `member.hashes` never populated, contradicting the spec (VERIFIED, medium)

Full analysis in specs-docs.md S1. `zip_reader.py:472` builds the member without `hashes=`, so
`member.hashes` is empty for every ZIP member despite `info.CRC` being available and
archive-data-model spec:193 mandating `"crc32"`. Blocks the founding dedupe use case for the most
common format. One-line population; not applied because it's a public data-model change deserving a
test + a decision on VerifyingStream involvement. QUESTIONS Q3.

## L4 — Directory `get_members_if_available()`: unbounded re-walk + free-threaded cache race (VERIFIED walk, SUSPECTED race, low-medium)

concurrency.md C2/C3 + specs-docs.md S2/S3. `_MEMBER_LIST_UPFRONT = True` makes the "scan-free
peek" do a full uncached `os.scandir` recursion every call, mutating `_uname_cache`/`_gname_cache`
unguarded — a `dict` data race under free-threading, and O(n) work behind a method sold as cheap.
Design decision (QUESTIONS Q2), not a unilateral fix.

## Tech debt (no user-visible bug today, but a trap for the next change)

### D1 — Private stdlib API dependency: `lzma._decode_filter_properties` (VERIFIED)

`sevenzip_reader.py:198-199` calls `getattr(lzma, "_decode_filter_properties")` — a **private**
CPython function with no stability guarantee. It's wrapped in `except (AttributeError, ...)` that
maps failure to `CorruptionError`. So if a future Python renames/removes it, **every** LZMA/LZMA2 7z
member would be reported as corrupt rather than failing loudly with "archivey needs updating." This
is the one place the native reader leans on a private stdlib internal; it deserves a
`hasattr`-at-import assertion (fail loud) rather than a silent per-member CorruptionError, and a
tracking test that would break on the Python that removes it.

### D2 — `capture_open_site` retains a full stack snapshot per reader (VERIFIED, minor)

`open_site.py:27` does `traceback.extract_stack()` on **every** `open_archive` and retains the full
`tuple[FrameSummary, ...]` for the reader's lifetime (for a possible `ConcurrentAccessError`
breadcrumb that most readers never hit). For the founding use case (opening millions of archives in
a dedupe sweep) that's a measurable per-open cost and retained memory for a rarely-used diagnostic.
Consider capturing lazily (only the file:line eagerly, the full stack on demand) or gating it behind
a config flag.

### D3 — Duplication debt (complexity.md X1–X4)

The handle-lock branch (×15), NTFS FILETIME conversion (×2), the ZIP exception tuple (×3), and the
ZIP STORED password loop are the standing "clean as you go" debt. None is a bug; each is a place a
future edit drifts one copy out of sync. X1 (handle-lock helper) is the highest-value cleanup.

### D4 — `SingleFileReader` eager-opens the decompressor for a non-seekable source (VERIFIED, minor)

`single_file_reader.py:148` opens `_pending_stream` at construction for a non-seekable source, so
`open_archive(pipe)` reads/initializes the decompressor immediately (gzip header, etc.) rather than
lazily on first read. On a pipe with no data yet, `open_archive` blocks. Probably intentional
(surface format errors at open), but it means "open" does I/O for this one backend where others defer.
Worth a one-line note in the backend docstring at minimum.
