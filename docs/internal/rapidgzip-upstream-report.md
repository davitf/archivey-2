# rapidgzip upstream report — soft EOF and related defects

Status: **documentation only — not filed upstream** (2026-07-20). Soft empty/short
success on truncated gzip is **by design** in rapidgzip’s parallel reader (trial-and-error
mid-stream decode for ratarmount / random access). Filing it as a “bug” would be wrong;
an `is_stream_complete()`-style API would be a **feature request** we are not opening
now. This note records the contract Archivey must work around, plus adjacent abort
defects that *are* bug-class.

Deep dive (code citations, issue table, repros):
`openspec/changes/rapidgzip-truncation-investigation/UPSTREAM_TRUNCATION_REPORT.md`.

Archivey product mitigation (empty→stdlib fallback + single-member ISIZE backstop):
**implemented** in `_GzipTruncationCheckStream` (OpenSpec change
`rapidgzip-truncation-investigation`). Accelerator shutdown / dual-load /
Python-source `terminate()`: `docs/internal/known-issues.md` (Bugs 1–3).

Pinned: **rapidgzip 0.16.0** ≡ librapidarchive `1221a30` (`[version] Bump rapidgzip
version to 0.16.0`). Soft-EOF paths unchanged on inspected HEAD.

---

## Classification

| Topic | Class |
| --- | --- |
| Soft EOF on truncated gzip / empty-short success | **by design** (not a bug) — Archivey limitation; mitigate with empty→stdlib + ISIZE. macOS raises more often than Linux/Windows but still silent at cut=10. |
| `std::terminate` after some path-source errors | **bug-class** — see known-issues Bugs 1/3 + §2 below |

## 1. Soft EOF on truncated input (by design — Archivey limitation)

### Behaviour

With path sources and `parallelization=0` (**all cores** in upstream’s API — intentional
in Archivey):

- Mid-body truncations of ordinary single-block gzip often make `RapidgzipFile.read()`
  return `b""` **without raising** (**Linux / Windows**; macOS mostly raises after cut=10).
- Multi-block / large streams often return a **correct short prefix** (or full payload if
  only the trailer is missing) **without raising** on Linux/Windows.
- Objects frequently report `block_offsets_complete=True` and `size == len(returned)`, so
  callers cannot tell a valid short member from a truncated stream via those APIs.
- Stdlib `gzip` sized-reads still yield a prefix then raise `EOFError`.

### Why (upstream)

- `ParallelGzipReader::read`: missing chunk → soft EOF, return bytes already written.
- `GzipChunkFetcher::processNextChunk`: `encodedSizeInBits == 0` → finalize block map,
  return empty.
- `GzipChunk::tryToDecode`: **swallows** `std::exception` while guessing block starts
  (expected during speculative decode).

CHANGELOG / empty-gzip handling also prefer not throwing when there is “no more
decodable data.” There is **no** Python docstring that truncated files must raise.
GitHub issues do not treat silent-empty `read()` as a user-facing bug.

### Archivey stance

- Do **not** file this as an upstream bug.
- Do **not** parse stderr (`Unexpected end of file when getting block…` is a **rethrow**
  path near the trailer, not a silent-success channel).
- Do **not** trust `block_offsets_complete` / `size` for completeness.
- Mitigate in Archivey: empty→stdlib fallback + ISIZE for non-empty silent EOF
  (see OpenSpec change).

---

## 2. Abort / `std::terminate` after errors (bug-class — already tracked)

Near-trailer truncations and CRC mismatches often raise `RuntimeError: std::exception`
(and may log the Unexpected-end stderr line) and then **abort** the process via
worker-thread finalization / GIL checks (`ScopedGIL` → `std::terminate`), including on
some **path** sources — not only Python file-object sources (Bug 3).

| Related Archivey notes | |
| --- | --- |
| Bug 1 — must `close()` accelerators | `known-issues.md` |
| Bug 3 — Python source raises → terminate | `known-issues.md` |
| Internal invariant | `std::logic_error` bit-buffer message on some multi-block cuts |

These remain **open upstream defect class** items; Archivey already sandboxes / closes
aggressively. Soft EOF (§1) is separate from this abort class.

---

## 3. API notes useful to Archivey

| Fact | Implication |
| --- | --- |
| `parallelization=0` → `availableCores()` | Archivey passes `0` **intentionally** (all-cores + benchmarks). Not “sequential.” |
| No `eof()` / `is_complete` / last-error | No first-class incompleteness flag today |
| `verbose=` | Stats/profile only — does not harden EOF |
| CLI maps EOF to exit 1 with a clear message | Python API has no equivalent status |

---

## 4. If we ever request an upstream feature

Only if product needs it later — **feature request**, not a bug report:

> Expose `is_stream_complete()` / similar set when decode stops without a verified
> gzip footer (CRC/ISIZE), without relying on stderr.

Draft body: `UPSTREAM_TRUNCATION_REPORT.md` §7. **Not filing now.**

---

## 5. bzip2 (`IndexedBzip2File`)

Shares soft-EOF shape for very short prefixes; mid-stream more often raises than gzip.
No ISIZE twin; container CRC covers archive members. Document only unless bare `.bz2`
parity is required.
