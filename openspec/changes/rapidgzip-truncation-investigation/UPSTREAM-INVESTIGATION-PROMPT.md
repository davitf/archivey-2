# Prompt: Investigate rapidgzip upstream truncation/corruption handling

Copy everything below the line into another agent session (with web/git access).

---

## Role

You are investigating the **rapidgzip** / **librapidarchive** upstream project to learn how it handles **truncated and corrupt gzip (and bzip2 via `IndexedBzip2File`)** inputs — especially cases where the Python API returns successfully with empty or short output instead of raising.

This is research for the Archivey library (`archivey-2`), which wraps rapidgzip as an optional seekable accelerator and needs a correct truncation strategy.

## Context Archivey already measured (do not re-derive; use as leads)

On Linux x86_64, rapidgzip **0.16.0**, path sources, `parallelization=0`:

1. **Silent empty is common** for ordinary single-block `gzip.compress` files: after a valid 10-byte header, mid-body truncations often `read()` → `b""` with **no Python exception**, while stdlib `gzip` sized-reads yield a correct prefix then raise `EOFError`. A bare `readall()` on stdlib raises with no return value — that hid streaming behaviour in early probes.
2. **Silent short / silent full** on multi-block (`Z_FULL_FLUSH`) or trailer-stripped inputs: rapidgzip returns a correct prefix (or full payload) and **does not raise**; stdlib still eventually raises.
3. On many silent-empty cuts, rapidgzip prints to **stderr** something like:
   `Unexpected end of file when getting block at 10 B 0 b (block index: 0) on demand`
   while still returning success to Python. Maintainer note matched: `10 OK 0` with that stderr line.
4. Near the trailer, rapidgzip often **raises** (`RuntimeError: std::exception`, etc.).
5. Some multi-block truncations **abort the process** (`terminate` / `std::logic_error`: “The bit buffer should not contain more data than have been read from the file!”).
6. Archivey’s current mitigation is an ISIZE trailer compare on path sources; under consideration: **empty→stdlib fallback** + keep/extend ISIZE for non-empty silent EOF. Knowing upstream’s intended signal (exception vs stderr vs ignore) would refine that.

Repos: https://github.com/davitf/archivey-2 — OpenSpec change `rapidgzip-truncation-investigation` (`FINDINGS.md` in that change). Upstream: https://github.com/mxmlnkn/librapidarchive (Python package `rapidgzip`; historically related to indexed_gzip / ratarmount).

## Your goals

Answer, with **code citations** (file + line or symbol) and **issue links** where relevant:

### A. Design intent

1. Does rapidgzip **intentionally** treat “incomplete last block / premature EOF” as a soft end (return what was decoded, or empty) rather than an error?
2. Is there a documented contract for truncated vs corrupt input (README, docs, comments, tests)?
3. Difference in policy between:
   - incomplete deflate block (no output yet)
   - complete blocks then missing gzip trailer (CRC/ISIZE)
   - corrupt Huffman / CRC mismatch
   - multi-member concatenation

### B. Signals we could capture

1. **Python exceptions:** which C++ error types are translated to Python, and which are swallowed? Search for `EndOfFile`, `Unexpected end`, `EndOfFileReached`, CRC, truncate, `std::exception`, `logic_error`.
2. **stderr / logging:** is the “Unexpected end of file when getting block…” line a deliberate log? Can it be disabled, redirected, or queried? Is there a callback, warning channel, or `errno`-like status after `read()`?
3. **API surface:** after `read()` returns `b""`, do objects expose useful state (`tell`, `tell_compressed`, `size`, `block_offsets`, `block_offsets_complete`, CRC helpers, “is_complete”, etc.) that would distinguish “valid empty member” from “truncated silent empty”?
4. **`parallelization`:** does worker-thread mode change truncation reporting? (Archivey uses `0`.)

### C. Implementation map

1. Clone or browse `mxmlnkn/librapidarchive` (and the PyPI `rapidgzip` 0.16.0 tag/commit if identifiable).
2. Trace path: Python `open` / `RapidgzipFile.read` → C++ reader → block fetch “on demand” → EOF handling.
3. Find where stderr is written for unexpected EOF and whether a Python exception is deliberately omitted.
4. Same for `IndexedBzip2File` if the path is shared or parallel.
5. Note any “ignore trailing garbage” / “stop at first error” / ratarmount-oriented design comments.

### D. GitHub issues / PRs / discussions

Search `mxmlnkn/librapidarchive` (and any mirrored `rapidgzip` / `indexed_gzip` issues if linked) for:

- truncation, truncated, incomplete, unexpected end of file, EOF, silent, CRC, ISIZE, trailer
- `std::logic_error` bit buffer
- Python returns empty / no exception
- abort / terminate on corrupt input

Summarize open vs closed issues: is silent EOF considered a bug, a feature for concatenated/partial archives, or untriaged?

### E. Practical recommendation for Archivey

Given priorities **(1) no silent success, (2) recover partial data, (3) keep seekability on good inputs**, recommend which upstream signals Archivey should use if any, e.g.:

- trust exceptions only + keep ISIZE
- capture/parse stderr (probably fragile — say so)
- poll post-read API flags if they exist
- open an upstream issue with a minimal repro (draft the issue body)
- empty→stdlib fallback is still necessary because upstream will not surface X

Be explicit when something is **absent by design** vs **bug** vs **unknown**.

## Constraints

- Prefer **path** file sources in repros (Archivey avoids Python file-object sources due to an upstream `terminate()` defect when the Python source raises — “Bug 3” in Archivey `docs/internal/known-issues.md`).
- Use subprocesses + wall-clock timeouts when executing rapidgzip on crafted truncations (hang/abort risk).
- Pin version: **rapidgzip 0.16.0** (current Archivey floor) and note if main/HEAD differs.
- Do not modify Archivey’s product decision; report findings for the maintainer.

## Deliverable format

1. **Executive summary** (½ page): intent + best signal for Archivey.
2. **Code map** with citations.
3. **Issue/PR table** (link, status, relevance).
4. **Repro snippets** (minimal) for silent-empty, silent-short, trailer-strip, and stderr-without-exception if still true on the commit you inspected.
5. **Recommendation** aligned to Archivey’s three priorities.
6. **Open questions** / suggested upstream issue draft if warranted.

## Starter commands

```bash
git clone https://github.com/mxmlnkn/librapidarchive.git /tmp/librapidarchive
cd /tmp/librapidarchive && git log -1 --oneline
# find python bindings / EOF handling
rg -n -i 'unexpected end of file|EndOfFile|truncat|ISIZE|CRC32|on demand' 
rg -n 'RapidgzipFile|def read' --glob '*.py'
pip index versions rapidgzip 2>/dev/null || true
```

Also search GitHub issues:

```text
repo:mxmlnkn/librapidarchive truncation OR truncated OR "end of file" OR EOF OR CRC
```

---

## Optional Archivey pointers (if the agent can read the other repo)

- `openspec/changes/rapidgzip-truncation-investigation/FINDINGS.md`
- `scripts/rapidgzip_truncation_sweep.py`
- `src/archivey/internal/streams/codecs.py` — `_GzipTruncationCheckStream`, `_translate_rapidgzip`
