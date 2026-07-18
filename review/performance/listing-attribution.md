# Listing attribution — per-format decomposition and fix worklist

**Measured at:** PR #143's tree initially; **L1/L2/L3 re-measured** on #146
(`cursor/listing-l1-l3-7360`). CPython 3.11, review host. All wall numbers are
warmed in-process medians (see `residual-gap.md` methodology). Written after
#143's small listing gains, to answer: *why* are ZIP/7z/RAR open+list still far
from their Q1 peers, and is `ArchiveMember` the problem?

**Answer in one line:** three different bottlenecks — ZIP is per-member
*derivation* (of which `ArchiveMember` construction is only ~20%), 7z was a
**parser byte-loop defect** (names read 2 bytes at a time — **fixed in #146
L1**), and the RAR ratio is currently a **fixture artifact** that measures
nothing about the parser.

## ZIP — 3.8–4.1× on many-small; overhead is derivation, not the dataclass

Fixture: 2,000 × 64 B STORED members. archivey open+list 19.1 ms vs `zipfile`
5.1 ms → **7.0 µs/member overhead**, decomposed by ablation (open-only /
open+`_to_member`-only / full `members()`):

| Stage | per member | share |
|---|---:|---:|
| fixed per-open cost (detection, info, etc.) | 0.29 ms/archive | ~4% |
| `_to_member` derivation (`zip_reader.py:547`) | **5.2 µs** | **72%** |
| registration + accounting + name index (`base_reader.py:769`) | 1.9 µs | 26% |

`ArchiveMember` construction micro-bench (20 k objects, no profiler):

| Variant | µs/object |
|---|---:|
| `ArchiveMember(type=…, name=…)` minimal | 0.64 |
| `ArchiveMember(...)` with ~20 kwargs (as `_to_member` calls it) | 1.51 |
| `@dataclass(slots=True)` clone, 10 non-None kwargs | 0.96 |
| `zipfile.ZipInfo(...)` (reference) | 0.34 |

So the object contributes ~1.5 µs of the 7 µs (~20%) — and half of *that* is
kwargs marshalling, not the class. Instance weight: 296-byte `__dict__` + 56-byte
object (no `__slots__`) — matters for million-member listings.
(**Post-#146 L2:** slots object is 256 B with no `__dict__`; see worklist.)

## 7z — 3.4× vs py7zr; the parser reads UTF-16 names 2 bytes at a time (defect)

> **Status:** fixed in **#146** (L1). Numbers below are the *pre-fix*
> attribution; after numbers are under L1 in the worklist.

Fixture: 2,000 × 64 B members, py7zr-written. archivey 62–67 ms vs
`py7zr.list()` ~18 ms → **3.4–3.6×**. cProfile: `_read_utf16` cumulative is
~58% of the whole listing; the census (`listing_probe.py sevenzip`) counts
**~45,000 `read_exact` calls per listing ≈ 22.5/member** — roughly one call
per name *character* plus a few per fixed field.

Mechanism (`sevenzip_parser.py`, pre-#146):

- `_read_utf16` loops `_read_exact(buffer, 2, …)` per UTF-16 code unit until
  the `\x00\x00` terminator.
- `_read_exact` runs a truncation pre-check per call:
  `_buffer_len(buffer) - buffer.tell()`, and `_buffer_len` is a
  `tell` + `seek(0, END)` + `seek(back)` triple.
- Net: ~5 Python calls + 3 BytesIO ops **per name character**. py7zr decodes the
  whole names blob with one C-level `decode("utf-16le")` + split.

## RAR — 2.42× vs rarfile is a fixture artifact; parser cost is unmeasured

The harness `rar_open_list` fixture (`tests/fixtures/rar/basic_solid__.rar`) is
**366 bytes / 6 members**. archivey 0.35 ms vs rarfile 0.14 ms — the entire delta
is *fixed per-open* cost: profiling shows **3 `io.open` + 7 `posix.stat` per
`open_archive`** (detection sniff + volume-sibling discovery) vs rarfile's ~1
open. Per-member parse cost cannot be measured from 6 members; no conclusion
about the RAR parser is currently supported by the harness number.
(Volume-discovery fast reject for non-volume-shaped names landed in **#146** L3;
a large RAR listing fixture is still outstanding.)

## Worklist (ordered; each item independent)

### L0 — ~~BLOCKER on #143~~ **done (#143)**

`normalize_member_name` fast path trailing-slash on FILE/SYMLINK — fixed +
regression test landed with #143.

### L1 — 7z: bulk-decode names; stop the per-read seek dance — **done (#146)**

1. `_handle_name` bulk-decodes the `kName` payload (`_decode_utf16_names`).
2. `_buffer_len` / `_buffer_remaining` are O(1) for `BytesIO` (no seek dance).

**After (same 2,000-member probe on #146):** archivey **24.8 ms** vs py7zr
12.6 ms → **1.96×** (was 3.4–3.6× / ~62 ms); `read_exact` **4.0/member**
(was ~22.5). Harness `sevenzip_open_list` realistic ~**2.2×** (was ~2.9×).
Still above the 1.25× native band — residual is non-name parse + model build.

### L2 — ArchiveMember: `slots=True` + skip None kwargs — **done (#146)**

`@dataclass(slots=True)` on `ArchiveMember`; ZIP/TAR `_to_member` stop
passing defaulted None/False fields.

**After (#146):** many-small ZIP probe **3.57×** / **4.71 µs/member** overhead
(derivation 3.28 + register 1.35); construction micro **0.42 µs** minimal /
**0.86 µs** full-kwargs; object **256 B** with no `__dict__` (was ~296+56).

### L3 — RAR: volume-discovery fast reject — **partial (#146)**

**Done in #146:** `discover_volume_siblings` returns `None` without a `stat`
when the name cannot be volume-shaped (ZIP/TAR/gz/plain `.7z` benefit).

**Still open:** committed large RAR listing fixture (needs the `rar` writer or
offline generation) so per-member parse cost can be measured vs `rarfile`.

### L4 — ZIP registration slice: measure-first — **deferred (#146)**

Registration is **1.35 µs/member** after L2 — no ≥10% lever found in #146;
left deferred.

### L5 — deferred (design change): lazy derivation

Unchanged — needs its own OpenSpec change. Only lever that gets ZIP many-small
listing near 1×; touches equality/accounting/listing contract.

## Repro

All numbers above reproduce with the committed probe (needs `[all]` for the
7z/RAR sections):

```bash
uv run --no-sync python review/performance/listing_probe.py zip       # ablation
uv run --no-sync python review/performance/listing_probe.py member    # micro-bench
uv run --no-sync python review/performance/listing_probe.py sevenzip  # + read census
uv run --no-sync python review/performance/listing_probe.py rar       # artifact demo
uv run --no-sync python -m benchmarks.harness --mode full --scale realistic
```

The `sevenzip` section's `read_exact` census is the L1 accept metric
(~22.5/member before the fix — O(name length); target a small constant per
member after). The `zip` section's per-member decomposition is the L2 accept
metric. On `main` without #143's fast paths the `zip` section reads slightly
higher (~9.4 µs/member overhead vs the 7.0 µs measured on #143's tree).
