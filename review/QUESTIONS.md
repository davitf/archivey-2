# Questions for the maintainer

Decisions I need before applying the corresponding change. Each has my recommendation and reasoning
so you can answer in one pass. Numbered for easy reply.

## Q1 — Broaden the materialization state-cleanup to `BaseException`? (concurrency C1 / latent-bugs L2)

`base_reader.py:503` uses `except Exception` around materialization. A `KeyboardInterrupt` /
`MemoryError` during `_iter_members()` leaves `cache_state = MATERIALIZING` forever — non-concurrent
readers then raise a misleading error, CONCURRENT waiters deadlock on the CV.

**Recommendation: yes.** Change the *state-cleanup* handler to `except BaseException: self._state.fail_materialization(); raise` (keep re-raising, so the interrupt still propagates). `mark_reader_closed` already handles `BaseException` this way, so this makes the two consistent. It touches a concurrency mechanism, which is why I'm asking rather than applying. I'd pair it with the red test in tests.md T2.

## Q2 — Directory backend: is a filesystem walk an "index" or a "scan"? (concurrency C2/C3, specs-docs S2/S3)

`DirectoryReader` sets `_MEMBER_LIST_UPFRONT = True` and reports `ListingCost.INDEXED`, so
`get_members_if_available()` does a full uncached `os.scandir` recursion every call — which
`archive-reading` spec:181 says it must *not* do ("without scanning"). The two specs disagree.

**Recommendation: treat the directory as `REQUIRES_SCANNING` and set `_MEMBER_LIST_UPFRONT = False`.**
Rationale: (a) it makes `get_members_if_available()` honest (returns `None`, no walk); (b) it removes
the free-threaded cache race (C2) for free, since the walk then only happens under the materialization
election; (c) it aligns directory with plain-tar, which already reports `REQUIRES_SCANNING` for the
same "no O(1) index, walk to enumerate" situation. The cost is that callers lose a cheap directory
peek — but there was never a cheap peek; it was an O(n) walk mislabeled. Alternatively, keep INDEXED
but *cache* the first walk and add a lock, and soften the `cost.py` `INDEXED` docstring — more code,
same honesty problem. I prefer the first option.

## Q3 — Populate ZIP `member.hashes["crc32"]`, and does it trigger VerifyingStream? (specs-docs S1, latent-bugs L3)

archive-data-model spec:193 mandates ZIP CRC32 under `"crc32"`; the backend never populates it,
breaking the founding dedupe use case for the most common format.

**Recommendation: populate it (`hashes={"crc32": info.CRC}` in `_to_member`), and keep relying on
zipfile's own CRC check — do NOT route ZIP reads through `VerifyingStream`.** Rationale: zipfile
already verifies CRC at EOF on every member read, so wrapping in `VerifyingStream` would double-hash
for no benefit and change the error path. Surfacing the datum (for dedupe/inspection) and keeping the
existing verification are separable; do the former. Needs a test asserting `reader.get(x).hashes["crc32"]`
matches, and one confirming read behavior is unchanged. (If you'd rather unify verification across
backends later, that's a separate change.)

## Q4 — What bound for the 7z parser's count fields? (latent-bugs L1, unknown-unknowns U1, threat-model O1)

`_read_files_info` pre-allocates `num_files` `_FileProps` with no bound → OOM on a crafted header.
This is threat-model O1 made concrete and undercuts VISION claim #2 in practice.

**Recommendation: bound every count field parsed from the header against the remaining header size,
plus a hard ceiling.** Concretely: `num_files` (and `num_folders`, timestamp/attribute counts)
cannot exceed `next_header_size` (each real entry consumes ≥1 byte of header somewhere), so a cheap
`if num_files > len(header_data): raise CorruptionError(...)` catches the pathological case without a
magic constant. Add a hard ceiling (e.g. a configurable `max_members`, tying into the "listing limits"
config the roadmap is missing) for defense in depth. I'd want this before the public release and the
Atheris gate. The exact ceiling / whether it's config-driven is your call — hence the question.

## Q5 — Is the strict-EOF-under-IGNORE-policy precedence intended? (other O-4)

`diagnostics_collector.py` raises `escalate_as` (TAR strict-EOF `TruncatedError`) even when the
per-code policy is `IGNORE`, with no diagnostic recorded. Two knobs, undefined interaction.

**Recommendation: keep the current precedence (strict-EOF wins), but spec it and add a test.**
`strict_archive_eof=True` is an explicit "make this fatal" that should outrank a per-code IGNORE.
Just make it intentional in the diagnostics spec rather than emergent. Low priority.

---

If you answer Q1/Q3/Q4 with "apply my recommendation," I can implement all three (each with the
red-green test named in tests.md) in a follow-up. Q2 is the only one that changes a cost value users
might read, so it's the one I most want your explicit call on.
