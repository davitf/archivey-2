# Upstream rapidgzip truncation behavior (research)

**Date:** 2026-07-20  
**Pinned package:** `rapidgzip==0.16.0` (PyPI; sdist built 2025-11-30)  
**Matching git:** `mxmlnkn/librapidarchive` commit `1221a30bb548b305a69e5715f2bc348ba37ac243` (`[version] Bump rapidgzip version to 0.16.0`)  
**Also inspected:** librapidarchive `HEAD` `ff5242bf439d996b81ead7fc0d1d9bc2dd0d7a6f` — `GzipChunkFetcher.hpp` soft-EOF / stderr paths are **unchanged** vs 0.16.0 (same file MD5). No newer PyPI release than 0.16.0.  
**Platform measured:** Linux x86_64, path sources, subprocess + wall-clock timeout.  
**Archivey call site:** `rapidgzip.open(..., parallelization=0)` / `IndexedBzip2File(..., parallelization=0)`.

> This document answers the OpenSpec change’s upstream questions. It does **not** change Archivey’s product decision; it feeds §2 (narrow / extend / remove ISIZE).

---

## 1. Executive summary

**Intent:** rapidgzip’s parallel reader is built for *trial-and-error* mid-stream decode (ratarmount / random access). Incomplete last blocks and premature EOF are often treated as a **soft end of stream**: return whatever was successfully decoded (including **empty**), finalize the block map as “complete”, and return success to Python. That is **absent-as-hard-error by design** for many truncations, not an accidental Python binding leak.

**Best signal for Archivey today:**

| Signal | Usable? |
| --- | --- |
| Python exceptions alone | **No** — silent empty/short is common; near-trailer often raises *and* can `std::terminate` |
| stderr (`Unexpected end of file when getting block…`) | **No** for silent cases (usually absent); accompanies raise/abort near trailer; not queryable |
| Post-`read` flags (`block_offsets_complete`, `size`) | **Mostly no** — truncated inputs often report `block_offsets_complete=True` and `size==len(short)` |
| `tell_compressed` after empty read | **Partial** — valid empty gzip ends at bit offset 160; header-only trunc stays at 0 |
| ISIZE trailer compare (current backstop) | **Still necessary** for silent-short and many silent-empty path sources |
| empty→stdlib fallback alone | **Insufficient** — large truncations return a long silent prefix, not empty |

**Verdict for Archivey’s three priorities:** keep (and do not shrink blindly) the ISIZE backstop for path gzip; treat exceptions as a bonus path; do not parse stderr; consider empty+`tell_compressed==0` as an extra cheap trap; open an upstream issue requesting a real incomplete-stream flag (draft below). Silent EOF is **by design** for the parallel decoder; process abort after some errors is a **related bug** (finalization / `std::terminate`).

---

## 2. Code map (with citations)

### 2.1 Python → C++ entry

- Cython: `python/rapidgzip/rapidgzip.pyx` (sdist: `rapidgzip.pyx`) wraps `ParallelGzipReader` as `_RapidgzipFile` / `RapidgzipFile`.
- `except +` on C++ methods → Cython translates `std::exception` subclasses to Python `RuntimeError` / `ValueError` (message from `what()`, or the useless `"std::exception"` when `what()` is empty).
- `EndOfFileReached` **does** inherit `std::exception` (`BitReader.hpp`), so it *can* surface — but many paths never let it reach Python.

```62:86:src/filereader/BitReader.hpp
    class BitReaderException :
        public std::exception
    {};
    // ...
    class EndOfFileReached :
        public BitReaderException
    {};
```

(Paths above are relative to the 0.16.0 sdist / `librapidarchive` tree under `src/`.)

### 2.2 Soft EOF (the silent-success path)

`ParallelGzipReader::read` treats a missing chunk as EOF and returns the bytes already written — **no exception**:

```570:585:src/rapidgzip/ParallelGzipReader.hpp
        if ( eof() || ( nBytesToRead == 0 ) ) {
            return 0;
        }
        // ...
            const auto blockResult = chunkFetcher().get( m_currentPosition );
            if ( !blockResult ) {
                m_atEndOfFile = true;
                break;
            }
```

`GzipChunkFetcher::get` returns `nullopt` when `processNextChunk()` fails to produce more data. Explicit soft-EOF when decode hit EOF with zero encoded size:

```350:355:src/rapidgzip/GzipChunkFetcher.hpp
        /* Should only happen when encountering EOF during decodeBlock call. */
        if ( chunkData->encodedSizeInBits == 0 ) {
            m_blockMap->finalize();
            m_blockFinder->finalize();
            return {};
        }
```

Also soft-finalizes when the next block offset is past EOF / finder exhausted (`processNextChunk` lines ~320–327).

### 2.3 Trial-and-error decode **swallows** exceptions

When guessing block starts, `GzipChunk::decodeBlock`’s `tryToDecode` **catches all `std::exception`** (including EOF / corrupt Huffman) and tries another candidate:

```728:733:src/rapidgzip/chunkdecoding/GzipChunk.hpp
                } catch ( const std::exception& exception ) {
                    /* Ignore errors and try next block candidate. This is very likely to happen if @ref blockOffset
                     * is only an estimated offset! ... */
                }
                return std::nullopt;
```

This is the architectural reason truncated mid-body input often becomes “no more chunks” rather than a Python error: failures are expected during speculative decode.

### 2.4 Stderr line + rethrow (near-trailer / on-demand path)

The Archivey-noted line is deliberate logging **before rethrow** — not a soft-success channel:

```650:661:src/rapidgzip/GzipChunkFetcher.hpp
            try
            {
                chunkData = BaseType::get( blockOffset, blockIndex, getPartitionOffsetFromOffset );
            }
            catch ( const gzip::BitReader::EndOfFileReached& exception )
            {
                std::cerr << "Unexpected end of file when getting block at " << formatBits( blockOffset )
                          << " (block index: " << blockIndex << ") on demand\n";
                throw exception;
            }
```

On 0.16.0 Linux measurements: this stderr line appears together with Python `RuntimeError: std::exception` (empty `what()` on `EndOfFileReached`), typically on **trailer-adjacent** cuts — **not** on the common silent-empty mid-body cuts.

CLI `main` maps the same exception to exit status 1 with a clearer message (`src/tools/rapidgzip.cpp` ~839–842: `"Unexpected end of file. Truncated or invalid gzip?"`). That contract is **CLI-only**; the Python API does not expose an equivalent status code.

### 2.5 Footer / CRC / ISIZE when a full member *is* decoded

When a last deflate block completes and the gzip footer is read, ISIZE and CRC are checked (if the header was seen):

```627:635:src/rapidgzip/chunkdecoding/GzipChunk.hpp
                    footer.gzipFooter = gzip::readFooter( *bitReader );
                    if ( didReadHeader ) {
                        if ( streamBytesRead != footer.gzipFooter.uncompressedSize ) {
                            // throw std::runtime_error("Mismatching size ...")
```

CRC mismatch surfaces as Python `ValueError: Mismatching CRC32 (...)`.  
**But:** after raising, worker-thread finalization often calls `std::terminate()` (see §2.7) — so “exception” is not always a clean recoverable signal.

### 2.6 `std::logic_error` bit-buffer message

```505:508:src/filereader/BitReader.hpp
        if ( UNLIKELY( position < bitBufferSize() ) ) [[unlikely]] {
            throw std::logic_error( "The bit buffer should not contain more data than have been read from the file!" );
        }
```

This is an internal invariant failure (can abort/`terminate` depending on context), not a documented truncation API. Archivey’s earlier abort observations fit this class of defect plus the GIL-finalization terminate path.

### 2.7 Process abort after errors (`std::terminate`)

```100:111:src/core/ScopedGIL.hpp
        if ( pythonIsFinalizing() || ( isLocked && ( PyGILState_Check() == 0 ) ) ) {
            // ...
            std::cerr << "Detected Python finalization from running rapidgzip thread.\n"
                         "To avoid this exception you should close all RapidgzipFile objects correctly,\n"
                         "or better, use the with-statement if possible to automatically close it.\n";
            std::terminate();
        }
```

Observed on trailer-strip / CRC-mismatch path sources even when using `with` in a short-lived subprocess: Python may tear down while C++ workers still run after an exception. Related to Archivey `known-issues.md` Bug 1/3 family (finalization / Python-source abort). **Path sources do not avoid all terminates** when an exception is raised mid-decode.

### 2.8 `IndexedBzip2File` (bundled bzip2)

Parallel path shares the same “soft EOF on missing block” shape (`ParallelBZ2Reader` / `BZ2Reader` `m_atEndOfFile`). Chunk decode for bzip2 **rethrows** EOF except at the very first bit of a chunk (`chunkdecoding/Bzip2Chunk.hpp` ~100–108). In practice on 0.16.0:

- Very short truncations → often **silent empty** (same complete-looking empty index).
- Mid/near-end truncations → more often **`RuntimeError: std::exception`** than gzip.
- No dedicated ISIZE-style trailer for Archivey to compare; container CRC (or stdlib) remains the guard if silent cases matter.

### 2.9 API surface after `read()` → `b""` / short

Exposed on `RapidgzipFile` (0.16.0): `tell`, `tell_compressed`, `size`, `block_offsets`, `available_block_offsets`, `block_offsets_complete`, `add_deflate_stream_crc32` / `set_deflate_stream_crc32s`, `file_type`. **No** `eof()`, **no** `is_complete`, **no** warning/status callback, **no** errno-like last error.

| After successful read of… | Typical state |
| --- | --- |
| Valid empty gzip (`gzip.compress(b'')`) | `size=0`, `tell_compressed=160`, `block_offsets_complete=True`, offsets `{80:0, 160:0}` |
| Header-only trunc (10 B) | `size=0`, `tell_compressed=0`, `block_offsets_complete=True`, offsets `{0:0}` |
| Silent short trunc | `size==len(short)`, `block_offsets_complete=True` — **indistinguishable from a complete shorter file** without external ISIZE/CRC |
| Full valid file | `size==payload`, `tell_compressed` past footer, complete=True |

**`verbose=`** only enables statistics / profile-on-destruction (`setStatisticsEnabled` / `setShowProfileOnDestruction`) — it does **not** turn soft EOF into exceptions. CLI `-q` / `--quiet` is CLI-only.

### 2.10 `parallelization`

```283:283:src/rapidgzip/ParallelGzipReader.hpp
        m_fetcherParallelization( parallelization == 0 ? availableCores() : parallelization ),
```

**`parallelization=0` means “use all cores”, not “sequential”.** Archivey’s `parallelization=0` is therefore **fully parallel**. Truncation *classification* (silent vs raise) did not meaningfully change for `0` / `1` / `2` in spot checks; abort risk is plausibly higher with workers + finalize.

---

## 3. Design intent vs corrupt / truncated policies

| Case | Upstream behavior (Python API, 0.16.0) | Classification |
| --- | --- | --- |
| Incomplete deflate; no output yet | Often `read()→b""`, success; index marked complete | **Soft EOF by design** (trial-and-error) |
| Complete deflate block(s), missing gzip trailer | Often **raise** (`RuntimeError: std::exception`) + stderr Unexpected end; frequently **SIGABRT** after | Raise intended; terminate is a **bug** |
| Silent short (multi-block / large stream) | Returns prefix of decoded bytes; **no** raise; `block_offsets_complete=True` | **Soft EOF by design** |
| CRC mismatch / ISIZE mismatch (footer reached) | `ValueError` with clear message; often then **terminate** | Error intended; terminate is a **bug** |
| Corrupt Huffman mid-stream | Speculative path may swallow; or raise/`ValueError`; sometimes abort | Mixed; speculative ignore is **by design** |
| Multi-member concatenation | Supported when intact; truncating first member → same silent patterns; truncating last trailer → raise/abort | Soft EOF + raise near trailer |
| Empty input / invalid magic | `ValueError: Failed to detect a valid file format.` | Hard error |
| Empty *valid* gzip | Success, empty payload (CHANGELOG: “Do not throw an EOF exception for an empty gzip…”) | Soft EOF **intended** |

**Documented contract:** README/CLI describe `--verify` / CRC for the tool; there is **no** Python docstring contract that truncated files raise. CHANGELOG mentions improving truncated-gzip *messages* (CLI) and not throwing on empty gzip — consistent with soft EOF for “no more decodable data”.

Maintainer stance on bad CRC (librapidarchive **#7**, open): prefer quitting with an exception when CRC is wrong because stream sync is unsure; recovery / stderr-only reporting is speculative. That issue is about *continuing after CRC*, not about silent truncation.

---

## 4. Issue / PR table

Searched via GitHub API on `mxmlnkn/rapidgzip` and `mxmlnkn/librapidarchive` (truncation, EOF, CRC, abort, logic_error, silent). No issue titles the silent-empty Python `read()` behavior as a bug.

| Link | Status | Relevance |
| --- | --- | --- |
| [librapidarchive#7 Don’t simply quit on bad CRC?](https://github.com/mxmlnkn/librapidarchive/issues/7) | **open** | Maintainer: bad CRC → quit; commenter incomplete bz2 hang — maintainer reproduced `RuntimeError: std::exception` not hang. Closest policy discussion. |
| [rapidgzip#28 End of file reached… gzip header](https://github.com/mxmlnkn/rapidgzip/issues/28) | closed | EOF while reading header / `--analyze`; not Python soft EOF. |
| [rapidgzip#5 Add CRC32 calculation](https://github.com/mxmlnkn/rapidgzip/issues/5) | closed | Historical CRC feature. |
| [rapidgzip#41 Sigabort BitBuffer::peekUnsafe](https://github.com/mxmlnkn/rapidgzip/issues/41) | closed | Abort class of bugs on master/packaged zlib. |
| [rapidgzip#26 / #24 GIL / PyMem abort](https://github.com/mxmlnkn/rapidgzip/issues/26) | closed | Python file-object / GIL aborts (Archivey Bug 3 family). |
| [rapidgzip#51 nested deflate crash](https://github.com/mxmlnkn/rapidgzip/issues/51) | closed | Crash on nested streams; not truncation soft EOF. |
| [rapidgzip#3 Harden with fuzzer](https://github.com/mxmlnkn/librapidarchive/issues/3) | open | Acknowledges decoder hardening still needed. |

**Summary:** silent truncated `read()→b""` / short success is **untriaged as a user-facing bug** and matches the parallel decoder’s speculative design. Hard CRC failure is intentional. Aborts/`terminate` on corrupt/truncated inputs are known-adjacent defects, not a truncation “feature”.

---

## 5. Repro snippets (0.16.0, path sources)

Run each in a **subprocess** with a timeout. Pin `rapidgzip==0.16.0`.

### 5.1 Silent empty (header-only / mid-body, small single-member)

```python
import gzip, subprocess, sys, textwrap, pathlib

pathlib.Path("/tmp/rgz_probe.py").write_text(textwrap.dedent("""
import sys, rapidgzip
p, par = sys.argv[1], int(sys.argv[2])
with rapidgzip.RapidgzipFile(p, parallelization=par) as f:
    data = f.read()
    print(len(data), f.tell_compressed(), f.size(), f.block_offsets_complete(), dict(f.block_offsets()))
"""))

payload = b"hello world " * 50
full = gzip.compress(payload, 9)
open("/tmp/t.gz", "wb").write(full[:10])  # or full[:20], full[:len(full)//2] for many silent-empty cuts
r = subprocess.run([sys.executable, "/tmp/rgz_probe.py", "/tmp/t.gz", "0"],
                   capture_output=True, text=True, timeout=10)
print(r.stdout, r.stderr, r.returncode)
# Expect: 0 0 0 True {0: 0}   and usually empty stderr
```

### 5.2 Silent short (large / multi-block)

```python
import gzip, os, zlib, subprocess, sys
# Random data → poor compression → long stream; half-file cut often returns a long prefix.
full = gzip.compress(os.urandom(50_000), 1)
open("/tmp/t.gz", "wb").write(full[: len(full) // 2])
# Same probe as above → OK with len≈24999, block_offsets_complete True, no exception.

# Multi-block (Z_FULL_FLUSH) also yields silent short at many cut points:
c = zlib.compressobj(6, zlib.DEFLATED, 16 + 15)
mb = b"".join(c.compress(b"X" * 2000) + c.flush(zlib.Z_FULL_FLUSH) for _ in range(5)) + c.flush()
```

### 5.3 Trailer strip (raise + stderr; often abort)

```python
full = gzip.compress(os.urandom(200_000), 1)
open("/tmp/t.gz", "wb").write(full[:-8])
r = subprocess.run([sys.executable, "/tmp/rgz_probe.py", "/tmp/t.gz", "0"],
                   capture_output=True, text=True, timeout=10)
# Expect: RuntimeError std::exception; stderr contains
#   Unexpected end of file when getting block at 10 B 0 b (block index: 0) on demand
# Often also: terminate called / Detected Python finalization... → rc < 0
```

### 5.4 Stderr without exception?

**Not reproduced** on 0.16.0 for the common silent-empty set: the Unexpected-end line is paired with rethrow. Archivey’s earlier “stderr + OK 0” for bare header may have been a different cut/tooling mix; on this pin, cut=10 is **OK empty with no that stderr line**. Valid empty vs trunc-empty still differ on `tell_compressed` (160 vs 0).

### 5.5 Valid empty vs trunc empty discriminator

```python
import gzip, rapidgzip
open("/tmp/e.gz","wb").write(gzip.compress(b""))
open("/tmp/h.gz","wb").write(gzip.compress(b"x"*100)[:10])
for p in ("/tmp/e.gz","/tmp/h.gz"):
    with rapidgzip.RapidgzipFile(p, parallelization=0) as f:
        assert f.read() == b""
        print(p, "tell_compressed", f.tell_compressed(), "offsets", dict(f.block_offsets()))
# e.gz → tell_compressed 160, offsets include 80/160
# h.gz → tell_compressed 0, offsets {0: 0}
```

---

## 6. Recommendation (Archivey priorities)

Priorities: **(1) no silent success, (2) recover partial data, (3) keep seekability on good inputs.**

1. **Do not trust exceptions alone.** Soft EOF is upstream design for the parallel reader. Removing `_GzipTruncationCheckStream` would regress (1) badly (silent empty *and* silent short).

2. **Keep ISIZE compare for path gzip** as the primary backstop for silent-short and many silent-empty cases. It is the only reliable length signal when `block_offsets_complete` lies. Multi-member limitations remain (proposal already notes them); do not treat “narrow to header-only” as sufficient — silent empty/short is **wide**, not a 10-byte curiosity.

3. **Optional cheap supplement (not a replacement):** if `read` yields empty and `tell_compressed()==0` (and/or offsets `{0:0}`), treat as truncated without waiting for ISIZE (covers header-only; does not cover silent-short).

4. **empty→stdlib fallback:** useful only as an *additional* probe for ambiguous empty results; **cannot** replace ISIZE for silent-short. Cost: double-open / lose seekability on that path unless carefully staged.

5. **Do not parse stderr.** Fragile, racy with workers, absent on the common silent path, and tied to raise/abort paths.

6. **Do not use `block_offsets_complete` / `size` as completeness.** They report the index of what was decoded, not “archive integrity”.

7. **Exception translation** (`_translate_rapidgzip`) remains correct for the cases that do raise; keep treating `RuntimeError`/`ValueError`/`std::exception` as corruption/truncation. Be aware some raises still **abort the process** — sandbox/timeout remains necessary (threat model).

8. **`parallelization=0`:** document internally that this means **all cores**. Switching to `1` is unlikely to fix silent EOF; it may slightly reduce finalize races (unproven).

9. **bzip2:** IndexedBzip2File raises more often than gzip on truncations but still has silent-empty early cuts; no ISIZE twin — rely on container bounds / stdlib when integrity matters.

10. **Upstream:** silent truncated success is **absent-by-design**; ask for an explicit incomplete/error flag rather than assuming a quick fix. Draft below.

---

## 7. Open questions / upstream issue draft

### Open questions

- Does HEAD after 0.16.0 change CRC-default or finalize/`terminate` behavior in ways that affect Python wrappers? (GzipChunkFetcher soft-EOF path: **no** on inspected HEAD.)
- macOS arm64: silent set assumed same (not measured in this run; task 1.3 still open).
- Can `size()` before full sequential read differ from post-read `size` on truncated inputs in ways Archivey could poll? (Spot check: unread object may report `0`.)
- Is there any non-Python C++ API flag for “stopped due to EOF mid-member” that wheels simply omit?

### Suggested upstream issue body

**Title:** Python API: truncated gzip often returns success with empty/short data and `block_offsets_complete=True`

**Body:**

```markdown
### Summary

With rapidgzip 0.16.0 on Linux x86_64, path sources, `parallelization=0` (all cores):
many byte-truncations of ordinary `gzip.compress(...)` files make
`RapidgzipFile.read()` return `b""` or a short prefix **without raising**, while
stdlib `gzip` raises `EOFError` after yielding a prefix. The object often reports
`block_offsets_complete() == True` and `size() == len(returned)`, so callers cannot
distinguish a valid short member from a truncated stream.

Near the trailer (e.g. strip last 8 bytes), we usually get
`RuntimeError: std::exception` plus stderr:
`Unexpected end of file when getting block at 10 B 0 b (block index: 0) on demand`
and frequently process abort (`terminate` / “Detected Python finalization from running rapidgzip thread”).

### Why this hurts

Libraries that wrap rapidgzip as a seekable accelerator (e.g. Archivey) need a
reliable incompleteness signal. Today the only practical backstop is comparing
decompressed length to the gzip ISIZE trailer on path sources.

### Ask

Is soft EOF on truncated input intentional for the parallel reader?
If yes, could the Python API expose an explicit flag/method such as
`is_stream_complete()` / `truncated()` / last-error after EOF, set when decode
stops without a verified footer (CRC/ISIZE), without relying on stderr?

### Minimal repro

(see §5.1–5.3 of Archivey’s UPSTREAM_TRUNCATION_REPORT; happy to attach a script.)

### Related

Code paths: `GzipChunkFetcher::processNextChunk` (`encodedSizeInBits == 0` → finalize),
`ParallelGzipReader::read` (`!blockResult` → soft EOF),
`GzipChunk::tryToDecode` swallowing `std::exception` during speculative decode.
```

---

## 8. Implications for OpenSpec change tasks

| Task | Implication from this report |
| --- | --- |
| 1.1–1.2 silent set | **Not** narrow to ~10-byte header-only; silent empty/short are common across mid-body and multi-block |
| 1.3 macOS | Still needed for confirmation |
| 1.4 bzip2 | Raises more than gzip; still silent-empty early; no ISIZE twin unless product requires it |
| 2.1–2.3 narrow/remove | **Removing** ISIZE is unsafe; **narrowing to header-only** is unsafe; **extend** multi-member carefully or keep single-member scope explicit |
| 2.4 AUTO | Keep ISIZE as a verifiability signal for AUTO bare `.gz` |

Measurement matrices for Archivey’s own fixtures can still be published from `scripts/` later; this report is the **upstream** characterization the change asked for.
