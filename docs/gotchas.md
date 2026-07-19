# Gotchas

Archivey’s one interface hides a lot of format history. Defaults stay on the cheap,
honest path — but some traps are still format law, stdlib behavior, or upstream
native code. If you read only one page after [Basic usage](usage.md), make it this
one.

Skim the bullets when debugging; follow the links when you need the full contract.
Deeper cost recipes live in [Access costs](costs.md); the full extraction policy
tables in [Safe extraction](safe-extraction.md); per-format matrices in
[Formats](formats.md).

## Seeking and redecompression

Member streams are forward-only unless you ask. Seeking inside a compressed member
is never free.

- Without `MemberStreams.SEEKABLE`, `seek()` raises — that is intentional.
- With `SEEKABLE` but no index or accelerator, a backward seek **re-decompresses
  from the start** (`STREAM_REWIND_REDECOMPRESSES`).
- Under `use_rapidgzip=AUTO`, rapidgzip is used only when seekability is declared
  and the known compressed input is large enough (about 1 MiB). Smaller members
  stay on stdlib codecs.

See [Access costs — seeking](costs.md#seeking-inside-compressed-members).

## Solid archives and open order

On solid 7z / RAR (and compressed TAR, which is solid for random member access),
opening members out of order can decode the same block repeatedly.

- Prefer one forward pass: `stream_members()` (or iterate in archive order).
- Named `open()` of a mid-block member may restart the solid block.
- `MemberStreams.CONCURRENT` makes overlapping streams *correct*; it does **not**
  remove solid open-order cost.

See [Access costs — solid archives](costs.md#solid-archives-prefer-one-forward-pass).

## Listing completeness vs damage

- `members()` / `scan_members()` — complete listing or raise.
- `members_report()` — always returns `MemberListReport`; check `error`
  (`None` means complete). Use this for inventory of messy archives.
- Iteration yields recovered members then raises on terminal archive listing
  damage (either access mode). Incomplete materialization is not treated as a
  successful complete list.
- Not the same as `--salvage` / best-effort resync (still future).

## Streaming mode is one pass

With `streaming=True`, the first of `__iter__` / `stream_members` / `extract_all`
consumes the pass — including after an early `break`. A second call raises.

- Use `scan_members()` to finish or drain when you need a full list after a partial
  pass.
- ZIP and ISO still need a seekable source today, even with `streaming=True`.
  Archivey will not silently buffer a pipe. A future native ZIP reader may improve
  the pipe case; ISO stays seekful.

## Passwords that look “accepted”

Wrong passwords usually fail loudly. A few format niches do not.

| Situation | What happens | What to do |
| --- | --- | --- |
| 7z AES + store/copy + **no** folder digest and **no** member CRC | Format-legal; wrong password can yield garbage (matches 7-Zip). Archivey emits `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`). | Treat the payload as unverified; prefer archives that store CRCs |
| 7z **header-encrypted**, wrong password | Usually fails; a rare decode that looks like an empty header is rejected as `EncryptionError` (never a silent empty listing). Residual: garbage that parses as a non-empty plausible header can still slip (inherent without a password check value). | Prefer a known-good password; don’t treat “0 members” as proof of emptiness without checking diagnostics/errors |
| ZipCrypto + multi-password + **STORED** | ~1/256 wrong candidates pass the weak open check → may CRC-scan the whole member | Prefer a single known password for huge stored members |
| 7z with several candidates | Each wrong try pays key derivation | List the most likely password first |

## Format limitations

These are not archivey bugs; they are format or stdlib constraints. Some may improve
with a future native ZIP/TAR reader — until then, plan around them.

| Limitation | Today | Later? |
| --- | --- | --- |
| TAR mid-archive corrupt header | Stdlib `tarfile` can treat it as clean EOF → silently short listing. Archivey raises `CorruptionError` **by default** when the stopped scan lands on a rejected (non-null) header block. A tar that just lacks the two-block null trailer (trailer-less / `cat`-joined, or truncated exactly at a member boundary) is warned via `ARCHIVE_EOF_MARKER_MISSING`; for inventory/dedupe use `ArchiveyConfig(strict_archive_eof=True)` to make that a `TruncatedError` too. | Native TAR walker |
| TAR corrupt **final** header, streaming | Caught in random-access reads; in forward-only `streaming=True` it surfaces as the missing-trailer warning, not `CorruptionError` (tarfile's stream layer hides the block). | Native TAR walker closes the gap |
| Multi-volume ZIP (`.z01`…`.zip`) | Detected and rejected (`UnsupportedFeatureError`) — rejoin first | May improve with native ZIP |
| ZIP/ISO from a pure pipe | Need seek; no silent spool | ZIP pipe reading may improve later; ISO stays seekful |
| ZIP UTF-8 flag “lie” (bit 11) | Stdlib may make the **whole** archive unlistable | May improve with native ZIP |
| RAR5 `-ver` history rows | Appear in `members()` as `path;1`, … with `is_current=False`; default **extract skips** them | — |
| RAR member **data** | Needs RARLAB `unrar` on `PATH` (not `unrar-free`). Listing works without it | — |
| BCJ2 (7z) | Rejected (`UnsupportedFeatureError`) — never garbage output | — |
| Gzip multi-member | Trailer CRC is last-member only — omitted from `member.hashes` | — |
| `.Z` truncation | Only nonzero leftover bits raise; zero-leftover cuts can stay silent | — |

## Names, duplicates, and hardlinks

Archive order and identity matter more than “the” name.

- `get(name)` is **last-wins** when names collide.
- `extract_all(members=["x"])` matches **every** member named `x`; pass an
  `ArchiveMember` when you mean one identity.
- Hardlink targets resolve to an **earlier** same-named member by `member_id`, not
  to “whichever `get` would return.”
- Members with `is_current=False` (for example RAR version history) stay visible in
  listings but are skipped on extract by default.

## Extraction

Extraction is safe by default, but “safe” still means policies, limits, and
sometimes a different path on disk than `member.name`. If you remember only three
things: **STRICT may rewrite names**, **case/Unicode collisions are deliberate on
every OS**, and **history / non-current members are listed but not extracted**
unless you ask.

| Need to know | Detail |
| --- | --- |
| Safe ≠ unlimited | Traversal, symlink escapes, and bombs are blocked; huge/hostile archives can still raise `ResourceLimitError` unless you raise limits. |
| STRICT rewrites some names | Trailing dots/spaces stripped; non-UTF-8 bytes percent-escaped. Disk path may differ from `member.name` — see `EXTRACTION_NAME_SANITIZED` / `requested_path`. |
| Collisions are first-class | Under `STRICT`/`STANDARD`, `README`/`readme` (and NFC/NFD twins) collide on **all** platforms. `OverwritePolicy` applies; `REPLACE` is not a silent merge — a collision diagnostic fires. Use `OverwritePolicy.RENAME` (`photo (1).jpg`) for intentional duplicates. |
| Reserved names / `:` | Rejected under `STRICT`/`STANDARD` on every platform (`CON`, `NUL`, `file:ads`, …). |
| `OnError.CONTINUE` ≠ ignore bombs | Per-member failures can continue; global bomb and listing guards still stop. |
| `OnError.STOP` is failures-only | Policy blocks are always recorded and continued; inspect the report (or exit `3` on the CLI) for `BLOCKED`. Abort-on-unsafe is a separate future opt-in. |
| `TRUSTED` still won’t traverse | Ownership / sticky bits only when allowed; path safety stays on. |
| Hardlinks + filters | Excluding a hardlink’s source can orphan the link (especially on streaming sources); `OnError` decides fail vs continue. |
| Symlink-hostile filesystems | Unlike `tarfile`, archivey does **not** copy target bytes through a symlink; you get a typed failure or skip. |
| Staging leftovers | `.archivey-tmp-*` under the destination are safe to delete (left only after hard kill / power loss). |
| Nested archives | Recursion is caller-driven; a zip-quine loops only if you loop. Bound depth/size yourself. |
| Listing vs extract limits | Bomb guards apply during **extraction**. `ListingLimits` apply when materializing `members()`; `stream_members()` is intentionally unguarded. |

Full policy tables: [Safe extraction](safe-extraction.md).

## Native libraries and process risk

Optional accelerators and some codec wheels are native code. Archivey hardens what it
can (close-on-finalize, a single accelerator library, bounded PPMd feeds); it cannot
promise process-proof behavior on every hostile input.

- Do **not** close a caller-owned source underneath a live accelerator-backed stream —
  some upstream defects abort the process rather than raise.
- For untrusted input under a hard latency budget, leave accelerators off
  (`AcceleratorMode.OFF`) or enforce your own timeout — crafted input can busy-loop
  in C++.
- `import archivey` installs a hang-safety guard inside pycdlib’s namespace. If the
  same process also uses pycdlib directly, it sees that guarded behavior (a strict
  superset of correct results on valid trees).

Details: [Access costs — accelerators](costs.md#accelerators-and-process-aborts),
[internal known issues](internal/known-issues.md).

## What we can only warn about

- Prefer `reader.diagnostics` / the extraction report over hoping something appeared
  in logs.
- Nested-archive amplification and metadata fidelity (xattrs / ACLs / forks) are not
  claimed beyond what the docs say.
- Concurrent hostile modification of the destination during extract is out of scope.

When something looks like a bug but is listed here as format law, check
[Formats](formats.md) and the [internal open-issues triage](internal/open-issues.md)
before assuming a silent failure.
