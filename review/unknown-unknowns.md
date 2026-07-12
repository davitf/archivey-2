# Theme 6 — Unknown unknowns

> **Post-review status (PR #73):** U1 FIXED (7z count bound). E1 FIXED (ZIP crc32 surfaced). U2/U4/U5/U6 remain as noted (case-insensitive FS is threat-model O2 backlog). E2/E3 remain micro-opts.

Assumptions about formats, filesystems, Python internals, and the OS that are either wrong, or
true-and-unexploited. Grounded in the code; marked VERIFIED / SUSPECTED.

## Wrong (or fragile) assumptions

### U1 — "The CRC-checked 7z header bounds allocation" — it doesn't (VERIFIED)

The 7z parser validates `next_header_crc` before parsing, which *feels* like it makes the header
trustworthy. It doesn't: an attacker crafting a malicious archive computes a valid CRC over
malicious bytes. `_read_files_info` then does `files = [_FileProps() for _ in range(num_files)]`
with an unbounded `num_files` (`sevenzip_parser.py:571-572`) — a 5-byte field can request 2⁴⁰
allocations. See latent-bugs.md L1. The general lesson: CRC integrity ≠ semantic bounds; every
count field parsed from hostile input needs an independent sanity bound (pack streams already have
`_MAX_NUM_STREAMS`; files/timestamps/attributes don't).

### U2 — `os.replace` atomicity and Windows (SUSPECTED, mostly handled)

`_write_file_atomic` relies on `os.replace(tmp, dest)` being atomic and cross-name. On POSIX this
is a rename(2) — atomic. On Windows `os.replace` maps to `MoveFileEx(REPLACE_EXISTING)`, which is
atomic for files but **fails if `dest` is open by another handle** (sharing violation) — common if
an antivirus or another process has the target open. The coordinator treats any `os.replace`
`OSError` as a per-member failure (correct), but there's no test for the Windows-open-handle case,
and no retry. Also: `os.replace` cannot replace a directory with a file, which `_prepare_destination`
handles by rmtree-ing a real directory first — but a **race** where the directory reappears between
rmtree and replace is unhandled (benign: surfaces as a failure).

### U3 — `datetime.fromtimestamp` range differs by platform (VERIFIED, handled well)

The code correctly wraps every `datetime.fromtimestamp` in `(ValueError, OverflowError, OSError)`
guards (`zip_reader.py:168`, `sevenzip_reader.py:177`, `tar_reader.py:443`) — because Windows
raises `OSError` for negative/huge timestamps where POSIX raises `ValueError`. This is a place the
"unknown unknown" was *already known*. Good. The only gap is the duplication (complexity.md X2).

### U4 — Case-insensitive / Unicode-normalizing filesystems (SUSPECTED; threat-model O2)

`written_paths` and `source_paths` are keyed by exact `Path` (`extraction.py`), but macOS (HFS+/
APFS normalize to NFD/NFC) and Windows (case-insensitive) collapse distinct member names to one
on-disk file. Two members `café` (NFC) and `café` (NFD), or `A`/`a`, extract to the same path but
count as distinct keys — so the anti-collision/overwrite bookkeeping can be wrong on those FSes.
Threat-model O2 tracks this as open; no test exercises it (tests.md T6). The bookkeeping should key
by the FS's identity (e.g. `os.path.normcase` + normalization) on those platforms, or the
OverwritePolicy check should stat-by-identity.

### U5 — `surrogateescape` round-trip assumes decode symmetry (SUSPECTED, low)

Several backends recover `raw_name` by re-encoding the decoded name with `surrogateescape`
(`tar_reader.py:428`, `zip_reader.py:462`). This round-trips *iff* the original decode used the
same codec and surrogateescape. For ZIP the code is careful to re-encode with the exact codec
zipfile decoded with (UTF-8 vs cp437 vs metadata_encoding). But a stored name that was *already*
valid in the target codec yet semantically different (e.g. cp437 bytes that happen to be valid
UTF-8) can produce a `raw_name` that differs from the true stored bytes. Low impact (raw_name is
advisory), but it's an assumption worth a test with adversarial cp437/UTF-8-ambiguous names.

### U6 — GC/finalizer ordering under free-threading (SUSPECTED, appears handled)

`ArchiveStream`'s finalizer releases the reader lease and may run `_close_archive` on the GC
thread. Under free-threading the GC can run concurrently with reader use. `ReaderState`'s lock
serializes it, and `_maybe_teardown`/`claim_teardown` are idempotent, so I believe it's safe — but
this is the kind of thing that's correct until a refactor moves an unlocked field access into the
finalizer path. Worth a stress test that GCs streams while another thread uses the reader
(tests.md T7).

## True and unexploited (make our lives easier)

### E1 — ZIP already stores CRC32; surface it (VERIFIED, high value)

`info.CRC` is on every `ZipInfo` and is thrown away. Populating `member.hashes["crc32"]` (S1) turns
on cheap dedupe for the most common format at zero read cost — the founding use case. This is the
single highest-value "we're not exploiting what's already there."

### E2 — Reflink/`copy_file_range` for cross-device hardlink fallback (low)

`_place_link` falls back to `shutil.copy2` on EXDEV (`extraction.py:955`). On Linux with a
CoW filesystem (btrfs/XFS), `os.copy_file_range` or a reflink (`FICLONE`) would make that fallback
near-free and space-shared. Minor, but the hardlink-heavy backup use case would benefit.

### E3 — `posix_fadvise(SEQUENTIAL)` / large read buffers on the extraction copy (low)

`_copy_to_fileobj` reads in 1 MiB chunks — fine. On the read side of a big sequential extraction,
`os.posix_fadvise(fd, POSIX_FADV_SEQUENTIAL)` on a path source would let the kernel read-ahead more
aggressively. Micro-optimization; only worth it once benchmarks exist (roadmap).

### E4 — The `size` fsspec convention is already well-exploited

Credit: `source_byte_size`'s whitelist + `try_get_size()` + the `.size` attribute chain is a
genuinely clever way to get nested-archive source sizes without decompressing, and it's used
consistently. Nothing to add — noting it as the model the rest of the "cheap metadata" code follows.
