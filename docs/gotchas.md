# Gotchas

Archivey's one interface hides a lot of format history. Defaults stay on the cheap,
honest path — but some traps are still format law, stdlib behaviour, or upstream
native code. If you read only one page after
[Reading members](reading-members.md), make it this one.

This page is a **digest**: one line per trap, and a link to the page that owns the
detail. A topic is here only if a caller choice is likely to shoot you in the foot,
or if Archivey cannot fully deliver on failing loudly and verifying. Format
matrices, policy tables and unsupported-feature lists live on their owning pages.

## What you should and shouldn't do

- **Don't seek backwards in a compressed member without meaning it.** Without
  `seekable_members=True`, `seek()` raises. With it and no index or accelerator, a
  backward seek **re-decompresses from the start** — loudly, via
  `STREAM_REWIND_REDECOMPRESSES`, but it still costs.
  → [Seeking](access-and-cost.md#seeking-inside-compressed-members)
- **Don't open members out of order in a solid archive.** On solid 7z / RAR and any
  compressed TAR, a named `open()` can restart the whole block. Prefer one forward
  pass. `concurrent_members=True` makes overlapping streams *correct*; it does not
  make them cheap.
  → [Solid archives](access-and-cost.md#solid-archives-prefer-one-forward-pass)
- **Don't expect a second pass in streaming mode.** The first of `__iter__` /
  `stream_members` / `extract_all` consumes it — including after an early `break`.
  → [Streaming is one pass](access-and-cost.md#streaming-mode-is-one-pass)
- **Don't assume a name identifies one member.** `get(name)` is **last-wins** when
  names collide, and a name in a selector matches **every** member with that name —
  `stream_members(members=["x"])` hands you each version in turn. Pass an
  `ArchiveMember` when you mean one identity.
  → [Duplicate names](opening-and-listing.md#duplicate-names-and-is_current)
- **Don't assume the file lands at `member.name`.** Under `STRICT`, trailing dots and
  spaces are stripped and non-UTF-8 bytes percent-escaped; case and
  Unicode-normalisation twins collide on **every** OS, not just Windows.
  → [Names change on disk](extracting.md#names-change-on-disk)
- **Don't `read()` a member from an untrusted archive without a size guard.**
  `read()` is unbounded, and `stream_members()` is deliberately outside
  `ListingLimits`. Chunk untrusted payloads.
  → [Limits](extracting.md#limits)
- **Don't recurse into nested archives without bounding it yourself.** The bomb
  tracker checks expansion for *individual* archives and is **not nesting-aware**, so
  a zip-of-zips can amplify past your limits one level at a time.
  → [Limits](extracting.md#limits)
- **Don't close a source underneath a live accelerator-backed stream.** Archivey
  contains the upstream fault and re-raises it as a normal Python error, so this is a
  clean failure rather than a crash — but the stream is still dead and the read still
  fails. → [Accelerators](access-and-cost.md#accelerators-and-source-lifetime)
- **Do turn accelerators off for untrusted input under a hard latency budget**
  (`AcceleratorMode.OFF`), or enforce your own timeout: crafted input can busy-loop
  in C++ where a Python timeout cannot cleanly interrupt it.
  → [Hardening notes](extracting.md#hardening-notes-for-callers)

## What you should be aware of

Places where Archivey **cannot** fully deliver "fail loudly and verify". None of
these are bugs; all of them are stated so you can decide whether they matter to you.

- **Archivey is stricter than the stdlib about damage.** Where `tarfile` and `gzip`
  often stop quietly, Archivey raises or emits a diagnostic. Code ported from the
  stdlib may start seeing errors on archives that "worked".
- **A wrong 7z password can yield garbage.** With AES plus store/copy and neither a
  folder digest nor a member CRC, the format offers no check value — matching 7-Zip.
  Archivey emits `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`). Treat the
  payload as unverified. → [7z](formats.md#7z)
- **A 7z header-decryption residual remains.** A wrong password that decodes to a
  plausible **non-empty** header can still parse; an empty one is rejected as
  `EncryptionError`, never a silent empty listing. Don't read "0 members" as proof of
  emptiness without checking diagnostics.
- **TAR has two honesty residuals.** A trailer-less or `cat`-joined tar is *warned*
  about, not raised — it is byte-identical to a truncation at a member boundary; set
  `strict_archive_eof=True` when you need a provably complete listing. And a corrupt
  **final** header is caught in random access but not in forward-only streaming.
  → [TAR](formats.md#tar-and-compressed-tar)
- **`strict_archive_eof=True` reads to the end of the file.** It requires every byte
  after the two-block trailer to be zero, so trailing junk and concatenated archives
  raise instead of passing silently. Zero padding still passes — `tar` writes 10 KiB
  records. The cost is the point of the flag being opt-in: the check is O(tail length),
  and on a `.tar.gz` the tail is decompressed to inspect it.
- **Truncation detection on bare gzip/zlib through rapidgzip is best-effort.**
  Upstream soft-EOFs by design and Archivey backstops it, but a residual hole
  remains. Use `use_rapidgzip=OFF` when you need certainty. This is about **bare**
  streams — ZIP/7z members carry their own CRC and fail properly.
  → [Single-file compressors](formats.md#single-file-compressors)
- **`.Z` truncation is partly silent.** Only nonzero leftover bits raise; a cut on a
  code boundary stays quiet.
- **`import archivey` patches pycdlib process-globally.** A hang-safety guard is
  installed inside pycdlib's namespace. Other code using pycdlib in the same process
  sees that guarded behaviour — a strict superset of correct results on valid trees.
  → [ISO 9660](formats.md#iso-9660)
- **An empty listing is a diagnostic, never an error.** A legitimately empty tar is
  10240 bytes, every one of them zero — byte-identical to a zero-filled junk file, so
  no rule over the bytes can reject one without rejecting the other. Archivey opens it,
  reports zero members, and emits `EMPTY_ARCHIVE` (plus
  `EXTENSION_FORMAT_UNCONFIRMED` when the format came only from the filename, or
  `EXPLICIT_FORMAT_LISTED_EMPTY` when you passed `format=` and detection disagrees).
  If "0 members" would mean something is wrong for you, check the count or use
  `detect_format()`, which does refuse zero-filled bytes.
  → [Errors and diagnostics](errors-and-diagnostics.md)
- **Prefer `reader.diagnostics` and the extraction report over logs.** Advisories are
  queryable data, not just log lines.
  → [Errors and diagnostics](errors-and-diagnostics.md)
