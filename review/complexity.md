# Theme 2 — Complexity & duplication

The codebase is, on the whole, well-factored: the `streamtools` base hierarchy
(`ReadOnlyIOStream`/`DelegatingStream`), the codec-descriptor table, and the
`SegmentedDecompressorStream` scaffolding all pull real weight and read cleanly. The
duplication that exists is concentrated in the backends. Concrete consolidations below,
ranked by payoff.

## X1 — The handle-lock branch is copy-pasted ~15× (medium payoff)

Every CONCURRENT-aware backend repeats this shape:

```python
if self._handle_lock is not None:
    with self._handle_lock:
        result = do_thing()
else:
    result = do_thing()
```

Occurrences: `tar_reader.py` (`_open_tarfile` init, `_iter_members`, `_iter_members_progressive`,
`_iter_with_data`, `_verify_tar_eof`, `_open_member`, `_close_archive` — 7), `zip_reader.py`
(`_zip_open_raw`, `_zip_close_raw`, `_close_archive` — 3), `iso_reader.py` (init, `_open_member`,
`_close_archive` — 3). Each is a place a future edit can forget the lock on one branch.

Sketch — a single helper on `BaseArchiveReader` (or a per-backend mixin), since the lock is
optional-or-`nullcontext`:

```python
# base_reader.py
from contextlib import nullcontext

def _handle_guard(self) -> ContextManager[object]:
    return self._handle_lock if self._handle_lock is not None else nullcontext()
```

Then every site collapses to `with self._handle_guard(): ...`. The one case that can't (the TAR
progressive walk holds the lock only around `next()`, not the yield) stays explicit. This removes
~12 branch pairs and the "forgot one branch" failure mode entirely.

## X2 — NTFS FILETIME conversion duplicated between ZIP and 7z (medium payoff)

`_NTFS_EPOCH_OFFSET = 11_644_473_600`, `_filetime_to_datetime(...)`, and the `_TimestampIssue`
dataclass exist in near-identical form in `zip_reader.py:143-174` and `sevenzip_reader.py:140-184`
(7z's variant takes `int | None`, ZIP's takes `int`; both return `(datetime|None, issue|None)`).
The FILETIME epoch math and the out-of-range guard are byte-for-byte the same.

Sketch — lift to a shared internal helper (e.g. `internal/timestamps.py`):

```python
def filetime_to_datetime(value: int | None, filename: str, *, field: str
    ) -> tuple[datetime | None, TimestampIssue | None]: ...
```

and have both backends import it. The `_TimestampIssue` shapes differ slightly (ZIP carries
`source`, 7z hardcodes `"ntfs"`); unify on the ZIP shape with a default. ~40 lines removed,
one place to fix a timestamp edge case instead of two.

## X3 — ZIP repeats its member-open exception tuple 3× (low-medium payoff)

The tuple `(zipfile.BadZipFile, RuntimeError, io.UnsupportedOperation, NotImplementedError,
zlib.error, lzma.LZMAError, UnicodeDecodeError, ValueError, OSError)` appears verbatim in
`_open_zipfile_member` (626), `_open_compressed_confirmed` (688-698), and `_ensure_link_target`
(878-887). A drifting edit to one (adding a newly-observed exception type) silently leaves the
others narrower.

Sketch — a module constant `_ZIP_MEMBER_READ_ERRORS = (...)` and a small
`_translate_and_raise(exc, member_name)` helper wrapping the shared "translate → stamp → raise /
else re-raise" body, which is *also* duplicated at each of those sites.

## X4 — ZIP STORED-password path re-implements the candidate/provider loop (low payoff, high re-read cost)

`_open_stored_confirmed` (`zip_reader.py:725-813`) hand-rolls the full candidate-then-provider
loop that `_PasswordCandidates.attempt` already encapsulates: `for password in
iter_candidates()`, then `while has_provider(): ask_provider(...)`, `record_success`, exhaustion
messaging. It duplicates this because it needs a *batched* disambiguation (one shared CRC pass
over all weak-check survivors), which `attempt()`'s one-candidate-at-a-time shape can't express.

That's a legitimate reason to diverge, but the result is the single hardest-to-follow method in
the ZIP backend (I re-read it three times). It would benefit from either (a) a `attempt_batch`
primitive on `_PasswordCandidates` that takes a `weak_ok` predicate + a `disambiguate(survivors)`
callback, so the loop lives in one place, or (b) at minimum a comment block naming the three
phases (collect-survivors / disambiguate / provider-fallback). This is a readability refactor, not
a bug — flagging for the "clean as you go" backlog, not urgent.

## X5 — `_iter_with_data` default has a no-op tail (trivial)

`base_reader.py:354-357`:

```python
if previous is not None:
    # Do not close here: the caller still holds the last yielded stream ...
    pass
```

The `if`/`pass` does nothing; only the comment matters. It could be a bare comment at end of
function. I left it — it's harmless and the `if` arguably documents the invariant (there *is* a
dangling `previous`). Noting so it's a conscious choice, not an oversight.

## What is appropriately factored (don't touch)

- The codec-descriptor pattern (`codecs.py`): one `StreamCodec` subclass per codec, detection
  data as class attributes, the registry iterating `STREAM_CODECS`. Adding a codec is genuinely
  one class. This is the opposite of the DEV god-module and should stay.
- `SegmentedDecompressorStream` + `_build_index_backwards`: the XZ and lzip backends share the
  backward-scan skeleton and differ only in the trailer parser. Correct amount of abstraction.
- `ReadOnlyIOStream`/`DelegatingStream`: the `readinto_passthrough` flag is a real hazard made
  explicit rather than magic. Good.
- The `ReaderState` machine is complex but that complexity is essential, not accidental —
  every token kind maps to a real access-mode rule. Don't "simplify" it.
