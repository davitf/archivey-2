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
- Add stream/codec Atheris targets for **all** archivey-owned standalone codecs in
  the default main-push partition (not “if budget allows”).
- Size the partitioned wall budget to fit the target set; prefer longer runs over
  dropping slices. Keep headers well-fed, but do not treat ~150s as a hard ceiling.

**Non-Goals:**

- Full extract inside Atheris (mutation harness keeps that role).
- OSS-Fuzz / custom libFuzzer mutators (Python-side fixup remains).
- Turning Atheris into a PR-matrix requirement.
- Replacing or shrinking the mutation harness.
- Fuzzing filter-only codecs (BCJ / Delta) as standalone streams — they need a
  composed chain; covered indirectly via 7z when present.
- Guaranteeing deep optional ZIP member codecs (PPMD/ZSTD/Deflate64) on every run —
  register when extras exist; skip-clean when absent.

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

### Stream layer — full codec set

Archivey-owned standalone stream codecs worth direct targets (hostile headers,
decode, seek-index / CLEAR, error translation), accelerators forced **off**:

| Codec | Why |
| --- | --- |
| unix-compress (`.Z`) | Vendored LZW; CLEAR seek points; already found Atheris crash via detect_format |
| xz | Seek-index / dual decoder state; footer enrichment |
| lzip | Member segmentation + seek points |
| gzip | Stdlib path + truncation/ISIZE; rapidgzip OFF |
| bzip2 | Block codec; indexed_bzip2 OFF |
| lzma-alone | Legacy FORMAT_ALONE |
| zlib | Content-probe cousin; wrapped deflate |
| zstd / brotli / lz4 / deflate64 | Optional extras — register targets; skip-clean if backend missing |

Filter-only (BCJ, Delta) stay out of the standalone stream set.

### Budget sketch (default main-push — illustrative, not a ceiling)

Grow total wall time to fit; exact seconds env-overridable; `budget_scale` multiplies.
Illustrative partition (~4–5 minutes before scale):

| Slice | Seconds (illustrative) | Notes |
| --- | --- | --- |
| 7z header | 45 | Largest pure-parser slice |
| 7z open | 20 | |
| rar_header | 30 | |
| rar open | 15 | Now actually runs |
| detect_format | 12 | |
| zip (deepened) | 25 | List + bounded read |
| tar | 10 | Shallow list |
| iso | 8 | Hard timeout unchanged |
| stream: unix_compress | 15 | Per-input timeout |
| stream: xz | 12 | Per-input timeout |
| stream: lzip | 10 | |
| stream: gzip | 10 | |
| stream: bzip2 | 10 | |
| stream: lzma_alone | 8 | |
| stream: zlib | 8 | |
| stream: optional extras (each) | 8 | skip if unavailable |

Workflow `timeout-minutes` should rise with the partition (e.g. 45–60).

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

### 4. Require stream/codec targets for all standalone archivey codecs now

Each codec in the investigation table gets its own Atheris target (or a tightly
shared runner parameterized by codec) in the default main-push partition.
Seekable-capable codecs run with seekable indexing on (the interesting crash
class). Per-input timeout where hang classes exist (ISO pattern). Accelerators
forced off. Optional-extra codecs skip-clean when the backend is absent.
**Rejected:** “unix-compress only, others if budget allows”; **Rejected:** relying
solely on detect_format to reach codecs; **Rejected:** a hard ~150s ceiling that
forces dropping stream slices.

### 5. Mutation harness stays complementary

No change to `ARCHIVEY_FUZZ` / pytest mutation role. Atheris adds coverage-guided
read/stream stress; mutation keeps whole-archive extract chaos.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Longer CI wall time | Accept ~4–5+ min partition; bump workflow timeout; `budget_scale` for soaks |
| Many stream targets dilute header exploration | Keep absolute seconds on 7z/RAR headers high; do not cut them to “fit” streams |
| ZIP fixup complexity / false confidence | Unit-test fixup; minority broken CRC; don't claim perfect deflate CRC synthesis |
| Stream hangs (LZW / xz / bzip2) | Per-input `SIGALRM` timeouts; accelerators off |
| AES/password ZIP paths need passwords | Seed known-password fixtures; try empty/`password` candidates; skip when crypto extra absent |
| unrar apt flaky on runners | Prefer hard fail so skip is visible; `\|\| true` only if apt flakes prove chronic |

## Open Questions

None blocking — per-codec second splits can be tuned after a green longer run.
How aggressively to synthesize deflate CRCs vs accepting typed corruption on
payload-only flips remains an implementation tuning choice, not a scope gate.
