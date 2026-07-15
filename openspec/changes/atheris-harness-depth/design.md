## Context

The Atheris gate (`tests/atheris_fuzz/`, `.github/workflows/atheris-fuzz.yml`) landed
in #78 with mutate-then-fixup for CRC-gated headers (#79/#81 for RAR). Triage of
recent failures (#116) confirmed fixup still works for 7z/RAR outer headers, but:

- Every green CI log shows `skipping rar: skip_unless not met` — `atheris-fuzz.yml`
  never installs RARLAB `unrar` (unlike `ci.yml`).
- ZIP listing still goes through stdlib CD parse, but **member open/read** now uses
  archivey `SlicingStream` + codec layer (+ WinZip AES) after
  `zip-native-codec-streams` / `zip-aes-decryption`. The Atheris ZIP target only
  lists names; ZIP CRC fixup only patches **stored (method 0)** payloads.
- Stream/codec bugs (unix-compress CLEAR seek collisions) were found only because
  `detect_format`'s inner-TAR probe opens a seekable codec stream — there is no
  dedicated stream target (deferred as a non-goal in the original harness design).

Mutation fuzz (`tests/test_mutation_fuzz.py`) does open+read+extract under caps, but
is random bitflip / not coverage-guided — complementary, not a substitute for
Atheris on dense pure-Python decode paths.

## Goals / Non-Goals

**Goals:**

- Make the RAR open slice actually run in CI.
- Exercise ZIP member **read** (bounded) under mutate-then-fixup of headers and
  compressed content, hitting archivey's codec/AES paths.
- Add stream/codec Atheris targets starting with unix-compress; allow other
  archivey-owned codecs as budget permits.
- Rebalance the partitioned budget without starving 7z/RAR header slices.

**Non-Goals:**

- Full extract inside Atheris (mutation harness keeps that role).
- OSS-Fuzz / custom libFuzzer mutators (Python-side fixup remains).
- Turning Atheris into a PR-matrix requirement.
- Replacing or shrinking the mutation harness.
- Guaranteeing deep coverage of every optional ZIP codec (PPMD/ZSTD/Deflate64) on
  the default ~150s budget — seeds + extras when present; skip-clean when absent.

## Investigations

### RAR open skip

`rar_open_available()` requires `find_rarlab_unrar()`. CI main test job installs
`unrar` via apt; atheris workflow does not → open target always skips. Header-only
target still runs (~30s, growing corpus). Fix is a one-line apt install mirroring
`ci.yml`.

### ZIP list-only vs native streams

After native codec streams, interesting ZIP bugs live in:

| Path | Reached by current Atheris ZIP? |
| --- | --- |
| CD / listing / name decoding | Yes (shallow open+list) |
| Local-header → `SlicingStream` → `open_codec_stream` | No |
| WinZip AES decrypt + codec | No |
| Member CRC `VerifyingStream` | No (list does not verify payload CRC) |
| Deflate/stored CRC fixup | Stored only today |

Mutation fuzz does read member streams, but without coverage guidance and with
whole-archive bitflips that often fail early CD/local consistency checks.

### Stream layer

Unix-compress is zero-dep core and hostile-input dense (CLEAR realignment, seek
points, header flags). XZ/lzip seek indexes and accelerator OFF paths are also
candidates; start with `.Z` because it already proved Atheris-valuable via
detect_format, then add others if budget allows.

### Budget sketch (default main-push, ~150–170s)

| Slice | Seconds (proposed) | Notes |
| --- | --- | --- |
| 7z header | 40 | Keep largest pure-parser slice |
| 7z open | 15 | Slight trim |
| rar_header | 25 | Keep deep |
| rar open | 15 | Now actually runs |
| detect_format | 10 | Slight trim |
| zip (deepened) | 20 | List + bounded read |
| tar (keep with zip or split) | 8 | Shallow list |
| iso | 8 | Hard timeout unchanged |
| streams (unix-compress ±) | 15 | New |

Exact seconds remain env-overridable; `workflow_dispatch` `budget_scale` multiplies.

## Decisions

### 1. Install `unrar` in the Atheris workflow (ship with this change's CI delta)

Mirror `ci.yml`'s apt install so `rar_open_available()` is true on ubuntu-latest.
**Rejected:** leaving skip-clean as "good enough" — the spec already lists RAR open
as a required partition when the backend is available; CI was making it unavailable.

### 2. Deepen ZIP in-place rather than a separate `zip_read` target

Extend `zip_tar` (or split ZIP vs TAR) so ZIP iterations: fixup → open → list a few
members → `open(member)` + bounded `read` (cap bytes / members). Keeps one ZIP
budget slice and forces coverage through the codec path.
**Rejected:** list-only forever; **Rejected:** full extract in Atheris.

### 3. Broaden ZIP mutate-then-fixup beyond stored-only

When recomputing CRC for method 0, keep current behavior. For deflate (and other
methods where the harness can leave compressed bytes intact), patch CD/local CRC
fields to match **zlib.crc32 of the uncompressed payload only when a cheap
recompute is possible**; otherwise prefer mutating compressed bytes while
**preserving** a previously valid CRC by re-fixing after payload-preserving
header edits, or mutate headers with fixup that recomputes CRC over known
uncompressed seeds embedded in the corpus.

Practical v1 approach:

- Seed from corpus ZIP members (stored + deflate + AES when fixtures exist).
- After mutation, for layouts that still parse as ZIP: recompute CRC32 over the
  local compressed payload **only for stored**; for deflate, either (a) leave CRC
  fields alone when only compressed bytes flipped (stdlib/archivey will
  CorruptionError on read — still useful), or (b) when only header fields outside
  the data region flip, recompute CRC from a decompressed oracle if cheap.
- Always keep minority broken-CRC samples.

**Rejected:** requiring unaided libFuzzer to solve CRC32; **Rejected:** stripping
CRC checks in fuzz builds.

### 4. Add `unix_compress` (and optionally other codec) Atheris targets

Direct `open_codec_stream(Codec.UNIX_COMPRESS, …)` with seekable ON/OFF variants or
a single seekable=True target (the crash class). Per-input timeout like ISO.
Accelerators forced off via existing fuzz `StreamConfig` / `ArchiveyConfig`.
**Rejected:** relying solely on detect_format to reach codecs.

### 5. Mutation harness stays complementary

No change to `ARCHIVEY_FUZZ` / pytest mutation role. Atheris adds coverage-guided
read/stream stress; mutation keeps whole-archive extract chaos.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Deeper ZIP/read + streams starve header budgets | Fixed partition table; headers stay largest; scale via `budget_scale` |
| ZIP fixup complexity / false confidence | Unit-test fixup; minority broken CRC; don't claim perfect deflate CRC synthesis |
| Stream hangs (LZW / xz) | Per-input `SIGALRM` timeouts; accelerators off |
| AES/password ZIP paths need passwords | Seed known-password fixtures; try empty/`password` candidates; skip when crypto extra absent |
| unrar apt flaky on runners | Same `\|\| true` pattern as ci.yml only if needed; prefer hard fail so skip is visible |

## Open Questions

None blocking proposal — budget exact seconds can be tuned during implementation
against a green main-push run.
