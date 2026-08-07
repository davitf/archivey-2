# Must explain — behaviours not inferable from signatures

Every item is something a competent user **will hit** that the type signature alone
does not reveal. For each: the concrete failure if nobody tells them. Claims cite
code or tests.

---

## 1. Two exception roots — `except ArchiveyError` does not catch your bugs

`ArchiveyUsageError` (and `ConcurrentAccessError`) sit **outside** `ArchiveyError`
(`exceptions.py:4–9`, `:150–170`). Overlapping `open()` without
`MemberStreams.CONCURRENT`, opening a member from another reader, or using a closed
reader raise usage errors (`tests/test_member_streams.py:28–60`, `:110–133`).

**If undocumented:** a broad `except ArchiveyError` around “untrusted input”
silently fails to catch the bug class, or the reverse — catching `Exception` and
treating usage errors as corrupt archives.

---

## 2. Default member streams: one live, forward-only

`open_archive` defaults to `member_streams=MemberStreams(0)` (`core.py:101`).
A second overlapping `open()` raises `ConcurrentAccessError` naming the open call
site (`tests/test_member_streams.py:28–43`). `seek()` raises
`io.UnsupportedOperation` unless `MemberStreams.SEEKABLE` was declared
(`tests/test_member_streams.py:74–92`).

**If undocumented:** thread-pool “open every member” code looks type-correct and
dies on the second stream; “seek back and re-read” looks like a BinaryIO and
fails.

---

## 3. `streaming=True` and `CONCURRENT` are mutually exclusive

Rejected at open with `ArchiveyUsageError` (`core.py:168–173`,
`tests/test_member_streams.py:95–98`).

**If undocumented:** users combine “pipe” + “parallel workers” and get a usage
error they cannot diagnose from either flag alone.

---

## 4. `streaming=False` refuses pipes; `extract()` does not

Random-access open on a non-seekable stream raises `StreamNotSeekableError`
(`core.py:234–243`). One-shot `extract()` peeks and **auto-opens streaming** for
non-seekable sources (`core.py:415–417`) because extraction is a single forward
pass.

**If undocumented:** `open_archive(sys.stdin.buffer)` fails; `extract(sys.stdin.buffer, dest)`
works — looks inconsistent. ZIP/ISO/7z still cannot stream from a pipe even with
`streaming=True` (`core.py:244–251`).

---

## 5. `extract()` has no member filter — by design

Docstring: selecting a subset needs the member list and would force a reopen
(`core.py:400–403`). Subset extraction is `open_archive` → `extract_all(members=…)`
(`reader.py:164–186`).

**If undocumented:** users search for `extract(..., members=)` / `members=` on the
one-shot API, invent workarounds, or extract everything then delete.

---

## 6. `OnError.STOP` still continues past safety blocks

`OnError` governs **failures** only. Policy `BLOCKED` (path traversal, special
files, unportable names) is always recorded and continued under either value
(`extraction_types.py:73–80`, `tests/test_extraction.py:661–674`). Real CRC /
write failures still raise under `STOP` (`tests/test_extraction.py:677–691`).

**If undocumented:** users set `STOP` expecting “abort on first hostile path” and
instead get a partial extract with `BLOCKED` rows and later members written.
`DiagnosticRaisedError` is the always-stop path (`exceptions.py:173–177`).

---

## 7. Bomb limits halt even under `OnError.CONTINUE`

Cumulative byte / entry / archive-wide ratio trips raise `ResourceLimitError` and
stop the whole run (`tests/test_extraction.py:721–731`, extraction coordinator
comments at `extraction.py:103–107`). Selector skips, filter skips, and rejected
members **do not** consume `max_entries` (`tests/test_extraction.py:761–812`).

**If undocumented:** “CONTINUE means keep going no matter what” is false for
bombs; “max_entries=1 with a huge archive but one selected member” unexpectedly
works.

Default limits are large but finite: 2 GiB extracted, ratio 1000 after 5 MiB,
~1M entries (`config.py:85–88`). A highly compressible member above the
activation threshold trips `max_ratio` (`tests/test_extraction.py:734–745`).
Below `ratio_activation_threshold`, extreme ratios are ignored to avoid tiny-file
false positives (`tests/test_extraction.py:326`).

---

## 8. Listing limits vs `stream_members` escape hatch

`ListingLimits` apply to `members` / `scan_members` / extract prep materialization
(`config.py:100–107`). `stream_members` is **unguarded** and can enumerate past a
cap that would fail `members()` (`tests/test_listing_limits.py:82–89`) — but
`extract_all` on scan-required backends still enforces the open-time caps
(`tests/test_listing_limits.py:115–130`). Passing a looser `config=` into
`extract_all` **cannot** raise the listing ceiling set at open
(`tests/test_listing_limits.py:103–112`).

**If undocumented:** “I raised limits in extract_all” silently does nothing for
listing; or users assume streaming iteration is always as capped as `members()`.

---

## 9. Duplicate names: last entry wins, first is `SUPERSEDED`

Multiple same-name members: earlier `is_current=False`, last `True`
(`base_reader.py:86–105`, `tests/test_duplicates_is_current.py:42–60`).
`get(name)` returns the last (`reader.py:134–135`). `extract_all` under default
`OverwritePolicy.ERROR` does **not** raise: non-current rows are `SUPERSEDED`,
disk content is the last (`tests/test_duplicates_is_current.py:63–93`).

**If undocumented:** users see two members named `file.txt`, call `get`, miss the
history entry, or expect overwrite errors that never come.

---

## 10. `stream_members` stream lifetime and laziness

Yielded stream is valid **only until the iterator advances**; `None` for
non-files (`reader.py:157–160`, `base_reader.py:500–502`). Streams are **lazy**:
open/decompress/password errors surface on first read, not on yield
(`base_reader.py:508–512`). Closing before read never opens the member
(`base_reader.py:536–542`).

**If undocumented:** collecting streams into a list then reading them fails;
“I iterated all members so passwords were checked” is false.

---

## 11. Solid / compressed-TAR access cost is orthogonal to concurrency

Compressed tar reports `AccessCost.SOLID` + `ListingCost.REQUIRES_DECOMPRESSION`
(`tests/test_cost_receipt.py:100–126`). Declared `CONCURRENT` does **not** remove
solid open-order cost (`types.py:32–33`, `core.py:122–123`). Out-of-order solid
block opens raise (`tests/test_solid.py:62–68`).

**If undocumented:** users enable `CONCURRENT`, fan out workers on a `.tar.gz` or
solid 7z/RAR, and get errors or catastrophic re-decompression cost with no
signature warning.

---

## 12. Mid-stream seekable sources start at `tell()`

A seekable stream is assumed to hold the archive **starting at the current
position**; detection peeks and restores; the opener wraps a zero-origin view
(`core.py:138–142`, `:253–256`). Same for `open_stream` (`core.py:323–324`).

**If undocumented:** users either manually slice (unnecessary) or forget to seek
to the embedded archive and parse the wrong bytes.

---

## 13. Passwords: wrong formats, candidate order, ZipCrypto STORED trap

A password on a format without encryption is **accepted, never consulted, and
recorded as `PASSWORD_ARGUMENT_UNUSED`** — in every form (a single value, a list, a
`PasswordProvider`). It is a keyring offered, not an assertion about this archive
(`archive-reading` §assertion vs resource). This changed in the simplicity &
consistency review batch; it used to raise `UnsupportedOperationError` for a static
value while a provider callable opened fine, which was an asymmetry reachable only by
wrapping your list in a lambda. A *wrong* password on an *encrypted* archive still
fails loudly. Candidate order matters — especially 7z key derivation cost
(`core.py:148–150`). For multi-candidate ZipCrypto **STORED** members, ~1/256
wrong passwords pass the one-byte check and confirmation may CRC-scan the full
member (`core.py:152–160`).

**If undocumented:** password on a plain tar raises; wrong-password lists on large
STORED ZIPs hang; providers that return the same bad password loop until
termination logic stops them (`tests/test_password.py:108–133`).

---

## 14. RAR data needs RARLAB `unrar` — not substitutes

`PackageNotInstalledError` if PATH `unrar` is missing or not RARLAB
(`rar_unrar.py:21–23`, `:45–53`). `unrar-free` / `unar` / `7z` are explicitly
unsupported. Listing can succeed (native parser) while data open fails.

**If undocumented:** “I installed the `[rar]` extra” (cryptography only) still
cannot read payloads; or a distro `unrar-free` looks present and fails oddly.

---

## 15. Optional extras → PARTIAL / NONE, not silent skip of the format

Registry keeps formats known when deps are absent; `format_availability` reports
`PARTIAL`/`NONE` + install hints (`registry.py:1–9`, `:151–167`). Single-codec
formats go `NONE` when their codec is missing; multi-codec containers go
`PARTIAL` (`registry.py:169–174`, `tests/test_registry.py`).

**If undocumented:** users assume “import archivey” means every keyword in
`pyproject` works, or that missing zstd makes TAR_ZST disappear from
`list_known_formats`.

---

## 16. Accelerators: AUTO silent fallback vs ON loud failure

`AcceleratorMode.ON` requires the package (`PackageNotInstalledError`); `AUTO`
falls back silently when absent or when seek was not declared (`config.py:19–31`).
For rapidgzip AUTO, compressed size must also reach
`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (1 MiB) and have a verifiable decompressed
size (`config.py:69–75`, `:129–131`). Backward seeks without an index emit
`STREAM_REWIND_REDECOMPRESSES` / `RewindWarning` (O(n) redecompress)
(`archive_stream.py:54–63`, diagnostics codes).

**If undocumented:** “I installed nothing and seek is slow” / “I set AUTO and
tiny members still paid accelerator setup” / ON without `[seekable]` crashes
open.

---

## 17. Extraction safety surprises beyond “no path traversal”

- `TRUSTED` still runs universal path/symlink/special-file checks
  (`filters.py:49–55`, `tests/test_extraction.py:184`).
- `STRICT` strips execute bits and ownership; `STANDARD` keeps execute
  (`filters.py:163–190`, `tests/test_extraction.py:275–292`).
- `STRICT`/`STANDARD` reject Windows reserved names and `:` **on every OS**
  (`filters.py:291–302`); `STRICT` rewrites trailing dot/space
  (`filters.py:304–309`).
- `OverwritePolicy.REPLACE` unlinks then creates — **never write-through** a
  pre-existing symlink (`extraction_types.py:67–68`,
  `tests/test_extraction.py:591–602`).
- Dest root that is a symlink **to a directory** is followed (trusted dest);
  dest that is a file or dangling symlink is never deleted on error
  (`tests/test_extraction.py:529–587`).
- `MemberType.OTHER` always rejected; `ANTI` is not a file (`types.py:218–220`).

**If undocumented:** users pick `TRUSTED` thinking “disable security”; execute
bits vanish under default `STRICT`; macOS names ending in `.` change on disk;
REPLACE appears to follow a planted symlink out of dest.

---

## 18. Hardlink orphans need a seekable second pass

If a selected hardlink’s source was excluded, seekable extraction re-reads source
content in a second pass and writes it at the **link path** (source name never
created) (`tests/test_extraction.py:1163–1165`, `:1418–1423`). Forward-only
sources cannot recover — failure follows `OnError`
(`tests/test_extraction.py:1182–1190`).

**If undocumented:** filtered extracts silently lose hardlinked content on pipes;
or users expect the source filename to appear on disk.

---

## 19. `member in reader` is identity, not name

`__contains__` is O(1) identity; non-`ArchiveMember` raises `TypeError` so `in`
cannot fall back to iteration (which would consume a streaming pass)
(`reader.py:117–124`). Wrong-reader member objects raise `ArchiveyUsageError` on
`open` (`tests/test_member_streams.py:110–121`).

**If undocumented:** `"file.txt" in reader` TypeErrors; members passed across
readers fail mysteriously.

---

## 20. Closing the reader does not invalidate already-opened streams

Post-close reader ops → `ArchiveyUsageError`; an escaped member stream remains
readable (`tests/test_member_streams.py:124–133`).

**If undocumented:** users either fear use-after-close on streams that still work,
or leak streams assuming reader close tears them down.

---

## 21. `open_stream` vs `open_archive` for the same `.gz`

`open_stream` decompresses a bare codec stream. Handing it a ZIP (path or bytes)
raises `UnsupportedFormatError` (`tests/test_open_stream.py:56–68`).
`open_archive` on a `.gz` yields a one-member archive (single-file backend).
Detection may upgrade `GZ` → `TAR_GZ` via inner-TAR probe (`detection.py:264–278`);
a `.tar.gz` extension with failed probe can report bare `GZ` without conflict
warning (`detection.py:282–288`).

**If undocumented:** wrong entry point; or “my file is named `.tar.gz` but
detect says GZ”.

---

## 22. Format conflict: magic wins, extension loses (with a diagnostic)

Conflicting extension vs magic → magic wins + `FORMAT_EXTENSION_CONFLICT`
(`tests/test_detection.py:92–`, `detection.py:297+`). Extension-only match is
merely `GUESS` (`tests/test_detection.py:65–72`).

**If undocumented:** renaming `payload.bin` to `.zip` “works” until content
isn’t ZIP; or users trust extension over `FormatInfo.confidence`.

---

## 23. CLI defaults diverge from the library

CLI extract default overwrite is **rename**; library default is **error**
(`cli/main.py` help ~273). CLI default destination is smart anti-tarbomb
layout (cwd vs `./<stem>/`, with post-hoist for scan-required formats)
(`extract_cmd.py:89–142`). Exit code **3** means completed extract with only
policy blocks (`exit_codes.py:8–10`).

**If undocumented:** scripts ported from CLI to API suddenly error on
collisions; or “extract into cwd” surprises when the archive has multiple tops.

---

## 24. `read()` is unbounded; `ArchiveMember` is mutable

`read()` loads full member bytes with no size guard (`reader.py:148–150`).
`ArchiveMember` is mutable for late-bound backend fields; callers must treat
instances as read-only and use `.replace()` (`types.py:315–317`). Equality
excludes hashes/diagnostics (`types.py:392–402`). `modified` may be naive or
aware; compare via `modified_utc()` (`types.py:438–458`).

**If undocumented:** memory bombs via `read()`; accidental mutation; sorting
members by `modified` raises or mis-orders across formats.

---

## 25. Multi-volume and directory overrides

Only 7z (concatenate) and RAR (open vol 1, unrar resolves siblings) accept
multi-volume sequences (`core.py:202–216`). Length-1 sequences ≡ scalar
(`core.py:144–146`). A directory path resolves to DIRECTORY, and a conflicting
`format=` is **rejected for a directory path** with `ArchiveyUsageError` — it is not
silently overruled (`#225`).

Note the scope: "rejected" is specific to the directory case, where honouring the
argument would hand back a reader over the wrong data. A wrong `format=` elsewhere is
still an override that wins — that is what the argument is for, and F7's answer to it
is a diagnostic on an empty listing, not a refusal.

**If undocumented:** `open_archive([a.tar, b.tar])` raises; a caller expects
`format=ZIP` on a directory path to be honoured or ignored, and gets neither.

---

## 26. Non-file members cannot be `open()`ed

Directories (and similar) raise on `open` across backends
(`tests/test_nonfile_open.py`). Use `stream_members` / extract for structure.

**If undocumented:** `for m in members: reader.open(m)` crashes on dirs.

---

## 27. `strict_archive_eof` default is warn, not error

Missing TAR end-of-archive marker → diagnostic / warning by default;
`ArchiveyConfig(strict_archive_eof=True)` → `TruncatedError`
(`config.py:135–137`, `tests/test_archivey_config.py:106–128`).

**If undocumented:** truncated tars “work” in defaults and fail only when a
hardening flag is set — looks like a regression when enabled.

---

## 28. Measurement is opt-in and open-scoped

`io_stats()` returns `None` unless `open_archive` ran inside
`enable_measurement()` (`measurement.py:16–18`, `reader.py:190–196`).

**If undocumented:** counters always zero/`None` despite reading data.

---

## 29. ISO pulls `pycdlib` and permanently patches its `collections.deque`

When the ISO backend loads, it replaces `pycdlib.pycdlib.collections` with a
cycle-guarding deque proxy (`iso_reader.py:23–27`, `:115–178`). Confined to
pycdlib’s namespace but process-wide for that library.

**If undocumented:** other code using pycdlib in-process sees archivey’s guarded
deque — surprising if documented nowhere.
