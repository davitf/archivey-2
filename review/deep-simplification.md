# Deep pass 2 — Structural simplification

Question posed: not "where is code duplicated" (the first pass's X1–X5, all fixed) but
"which abstraction, if adopted, deletes a *category* of code — and is the half-size,
strictly-no-weaker version of this library real?"

Baseline: `src/archivey` is ~16.5k lines. Verdict up front: **the half-size version does not
exist** (argued at the end), but three category-deleting changes do, worth roughly 8–12% of
the tree and — more importantly — each deletes a *recurring failure mode*, not just lines.
They are ordered by (category deleted ÷ disruption).

## S1 — One error boundary: backends stop writing translate/stamp/raise code at all

**The category:** hand-rolled exception translation at backend call sites. The prior pass
saw one symptom (X3, ZIP's thrice-repeated tuple) and fixed it locally with
`_reraise_member_error`. The disease is tree-wide: the pattern

```python
except <raw errors> as exc:
    translated = self._translate_exception(exc)
    if translated is not None:
        self._stamp_error_context(translated, ...)
        raise translated from exc
    raise
```

exists at **10 sites today** (`tar_reader.py:245,274,305,336,509`,
`iso_reader.py:292,358,490`, `zip_reader.py:628,733`) plus the per-backend helper variants
(`_translate_open_error`, `_reraise_member_error`) — and this is *after* X3 was fixed. Every
site is a place to forget a member-name stamp, to catch too narrow a tuple (the exact bug X3
documented), or to run diagnostics under a handle lock (which `tar_reader.py:495-513`
carefully hand-avoids, once, with a comment).

**The observation that deletes it:** this logic already exists, once, finished, in
`ArchiveStream._fail` (`archive_stream.py:221-243`) — translate via the backend's hook,
stamp, chain, pass ArchiveyError through, re-raise unrecognized. Backends re-implement it
only because they call raw libraries *outside* an ArchiveStream: at archive-open time, at
member-open time, and during metadata passes.

**The change:** give `BaseArchiveReader` one context manager that *is* the error boundary —

```python
@contextmanager
def _translated_errors(self, member_name: str | None = None):
    try:
        yield
    except ArchiveyError as exc:
        self._stamp_error_context(exc, member_name); raise
    except Exception as exc:
        translated = self._translate_exception(exc)
        if translated is None: raise
        self._stamp_error_context(translated, member_name)
        raise translated from exc
```

and make it the *only* sanctioned way for a backend to touch its underlying library. Every
one of the 10 sites collapses to `with self._translated_errors(name):`. The per-backend
raw-exception tuples (`_ZIP_MEMBER_READ_ERRORS`, `_PYCDLIB_ERRORS`, the tarfile catches)
stop being *catch* lists at N sites and become what they already are conceptually: input to
the one `_translate_exception` hook. Two structural consequences beyond line count:

- The "unrecognized exceptions propagate raw" contract (CONTRIBUTING's no-catch-all rule) is
  enforced by the boundary's shape instead of re-earned at every site.
- The ZipCrypto special case (EncryptionError must not get member-stamped,
  `zip_reader.py:626-633`) becomes a keyword on the boundary instead of a divergent helper.

Estimated deletion: ~120–150 lines of live code, and the entire "site drifted" bug class.
This is the change X3 was a local instance of; adopting it retires X3's fix too.

**Not weaker because:** the boundary is behavior-identical at each site (same translate, same
stamp, same chaining); the only sites that can't use it verbatim are the ones that must
capture-then-translate to keep `emit()` outside a lock — and those become
`with self._handle_guard(): ...` *inside* `with self._translated_errors(...)`, which orders
correctly by construction.

## S2 — One member-list pipeline instead of two

**The category:** the parallel materialized/progressive machinery in `BaseArchiveReader`.
Today there are two complete pipelines that both produce "the resolved member list +
name index":

| concern | random-access pipeline | streaming pipeline |
|---|---|---|
| state | `_members_cache`, `_members_by_name_lists` | `_pass_scanned`, `_pass_by_name_lists`, `_progressive_gen`, `_forward_pass_started` |
| id stamping | `_get_members_registered` loop (`base_reader.py:490-494`) | `_stamp_progressive_member` (`:705-719`) |
| link resolution | inline second phase (`:496-513`) | `_finalize_pass_links` (`:692-703`) |
| completion | `complete_materialization` | `_ProgressivePassIterator.__next__` EOF arm |
| plus | `_get_members_index_only` (`:531-536`), a third id-stamping loop for `get_members_if_available` | |

Three id-stamping loops, two name-index builders, two link-resolution drivers, two
completion protocols — and the deep-concurrency findings N1 (partial-list publication) and
N2 (two-store snapshot race) each live in exactly one of the two pipelines, which is the
tell: state that exists twice gets its invariants enforced once.

**The unifying observation:** materialization *is* a drained forward pass. The random-access
pipeline is "run the progressive pass to completion eagerly, then do the link-read phase".
If `_get_members_registered` were implemented as

```python
def _get_members_registered(self):
    if snapshot := self._snapshot: return snapshot
    if owner := self._state.begin_materialization():
        for _ in self._begin_forward_pass(): pass      # same iterator, drained
        ... link-read phase over the pass's output ...
        publish(one Snapshot object)                    # single-field, fixes N2
```

then `_pass_scanned`/`_pass_by_name_lists` *are* the only accumulation state,
`_stamp_progressive_member` is the only id-stamper, and `_finalize_pass_links` merges into
the one publication point. Publishing a single `_Snapshot(members, by_name)` object (one
attribute store) makes the N2 fix structural instead of ordering-by-comment, and making the
pass iterator remember a mid-pass failure (the N1 fix) then protects *both* access modes,
because there is only one pass.

Estimated deletion: ~100–130 lines net, minus one whole class of "which pipeline stamped
this member" questions. `_get_members_index_only` stays (it is genuinely different: no
registration cost for index-only peeks) but shrinks to a pure enumeration.

**Not weaker because:** observable behavior is unchanged for every documented contract —
same election, same laziness, same streaming semantics (`_begin_forward_pass` is already
shared); the only behavior changes are the two bug fixes.

## S3 — One pass driver: `_iter_with_data` stops being an override point

**The category:** the "close previous / open current / yield / cleanup tail" loop skeleton
exists three times — base default (`base_reader.py:355-373`), TAR override
(`tar_reader.py:312-340`), 7z override (`sevenzip_reader.py:604-643`) — each with its own
subtly-commented invariant about the last yielded stream staying open, each with its own
error-translation wrapper (S1 again), and each a place where the previous-close invariant
can regress independently. The RAR reader (Phase 7, imminent per PLAN) would add a fourth.

**The change:** the base owns the single driver loop; backends override a narrow hook —
"give me this member's stream within the current pass":

```python
def _pass_open_member(self, member, ctx) -> ArchiveStream | None: ...
```

where `ctx` is per-pass state the base threads through (TAR: nothing — it opens via
`extractfile` under the handle guard; 7z: the current `SolidBlockReader`, swapped when
`folder_index` changes; base default: `_lazy_member_stream`). The 7z solid-block swap
(`close old block, open new`) fits as a second tiny hook (`_pass_advance(ctx, member)`), or
7z keeps a generator-based ctx. The driver enforces close-previous-on-advance,
close-current-on-finally, and skip-costs-nothing exactly once.

Estimated deletion: ~60–80 lines now, plus every future backend's copy; more valuable, the
`stream_members` ownership contract (the docstring at `base_reader.py:337-353`, currently a
"MUST override correctly" instruction to backend authors) becomes machinery instead of
documentation. This directly serves the stated plan: native RAR is next, and its
`unrar p`-pipe pass is exactly this shape.

## S4 — (Flagged, not recommended now) ReaderState's five overlapping admission mechanisms

`ReaderState` enforces "one pass XOR many workers, with internal exceptions" via five
interacting mechanisms: root tokens, child tokens, worker tokens, `_internal_open_depth`,
and `_gate_exempt_depth`. The deep-concurrency findings N3 and N4 are both artifacts of this
shape: the depth counters erase *who* is exempt, and the token sets erase *which thread*
owns what. A single explicit mode machine —

```
mode: IDLE | PASS(owner_token, owner_thread, internal_depth) | WORKERS(count)
```

— expresses the same admission table in one match statement, makes thread-scoped exemption
(the N3 fix) and owner-thread re-entry detection (the N4 fix) fall out of stored fields
rather than new bookkeeping, and would likely *shrink* `reader_state.py`. I flag rather than
recommend it because the first review's advice ("the ReaderState machine's complexity is
essential — don't simplify it") is half right: the *rules* are essential, but the current
*encoding* of the rules is where N3/N4 hid. If N3/N4 are fixed by patching the existing
counters, expect this file to accrete a sixth mechanism; if the maintainer is touching it
anyway, the mode machine is the version worth writing. Decision-sized, so it goes to
QUESTIONS rather than a recommendation.

## What is NOT worth abstracting (checked and rejected)

- **The accelerator exception-message tables** (`codecs.py:562-713`): irreducible essential
  knowledge about rapidgzip's per-platform error strings. Any abstraction would just move
  the strings.
- **The codec descriptor table itself**: already the exemplar (one subclass per codec); the
  first review was right to protect it.
- **`_zip_timestamps` / 7z timestamp handling**: post-X2 the FILETIME math is shared
  (`internal/timestamps.py`); what remains per-backend is genuinely per-format field layout.
- **The two `LockedStream` variants**: 20 lines each, deliberately different lock scopes
  with a documented reason (`locked.py:71-78`). Merging them behind a flag would obscure the
  one design fact that matters.
- **`streamtools`**: clean, dependency-free, correctly layered. Leave it alone.

## The half-size question, answered honestly

No — there is no strictly-no-weaker archivey at ~8k lines. The mass breaks down roughly as:
format parsers and per-format metadata fidelity (7z parser ~1k, ZIP ~1k, TAR/ISO/single-file
~1.3k), the codec layer including per-library error taxonomies (~2.5k), extraction safety
(~1.2k), streams/streamtools plumbing (~1.7k), and the public data model / config /
diagnostics surface (~2k). Almost all of that is *knowledge* — hostile-input hardening,
library quirks, platform divergence — that a smaller library would simply not have, i.e.
would be weaker in exactly the dimensions VISION stakes out (hostile input as first-class,
uniform error contract, honest cost signals). S1–S3 above total maybe 300–350 net lines
(~2%… but the deletions are concentrated in `base_reader.py` + backends, where they remove
perhaps a quarter of the *coordination* code, which is where the review findings cluster).
The honest framing: this library's size is in its knowledge, its risk is in its
coordination, and the simplifications worth doing are the ones that shrink coordination —
S1/S2/S3 do, a generic "make it smaller" pass would not.

## Correction to the first review

`complexity.md` X3's fix (`_reraise_member_error` + the shared tuple) treated a tree-wide
pattern as a ZIP-local one; the same drift hazard it closed in ZIP remains open at 8 sites
in TAR/ISO today (list under S1). Not wrong, but under-scoped — S1 is the finish of that
thought.
