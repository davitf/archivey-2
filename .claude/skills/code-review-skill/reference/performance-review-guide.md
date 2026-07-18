# Performance Review Guide

Performance review for a **Python archive library**: open / list / stream / extract.
Ignore frontend Web Vitals, CSS, JS bundles, SQL/ORM, and HTTP API latency — none of
those apply here.

In-repo context: `VISION.md` performance budget (≤ ~1.3× stdlib on common ZIP/TAR
paths), solid-archive cost model, and “never decompress the same byte twice” silently.

## Table of Contents

- [I/O and Streaming](#io-and-streaming)
- [Memory Management](#memory-management)
- [Algorithmic Complexity](#algorithmic-complexity)
- [Concurrency & Parallelism](#concurrency--parallelism)
- [Performance Review Checklist](#performance-review-checklist)

---

## I/O and Streaming

### Prefer streaming over full buffering

```python
# ❌ Slurp entire member into memory
data = archive.read(member)
process(data)

# ✅ Stream when callers only need a file-like
with archive.open(member) as fh:
    for chunk in iter(lambda: fh.read(1024 * 64), b""):
        process(chunk)
```

**Review points:**
- [ ] Hot paths stream member data instead of building giant `bytes` objects
- [ ] Listing / metadata does not force full decompression
- [ ] Solid archives: a pass does not re-decompress the same block per member silently
- [ ] Seek / reopen patterns are intentional and documented in the cost model
- [ ] Temp files (if used) are bounded and cleaned up

### Avoid redundant work

- [ ] Same compressed region not decoded repeatedly in one operation
- [ ] Format detection does not reread large prefixes without need
- [ ] Hash / CRC helpers use stored digests when the format provides them
- [ ] Extract + re-read patterns in tests are intentional (oracle), not copied into library code

## Memory Management

### Common leaks / growth bugs in this codebase

```python
# ❌ Unbounded buffer growth
buf = b""
while True:
    chunk = fh.read(65536)
    if not chunk:
        break
    buf += chunk  # quadratic + holds entire member

# ✅ Fixed-size processing or bytearray with known size
# or stream out without retaining
```

**Review points:**
- [ ] File handles / archive objects closed (`with`, `close()`, context managers)
- [ ] No accidental retention of full member payloads on long-lived archive objects
- [ ] Caches (solid block, decompressor state) have clear lifetime / invalidation
- [ ] Generators / iterators are used for large listings where appropriate
- [ ] Tests that build huge fixtures clean up temp dirs

### Detection

- Local: `tracemalloc`, careful reading of streaming APIs
- CI: existing benchmarks under `benchmarks/` when touching hot paths

## Algorithmic Complexity

### What to watch

| Pattern | Risk in archives |
|---------|------------------|
| O(n²) nested member loops | Re-scan of all members per member |
| Repeated solid-block decode | Wall time explodes with member count |
| Full-buffer then parse | Memory + copy cost |
| Linear search on hot path | Prefer maps/indexes already built at open |

```python
# ❌ O(n²): open/decompress per lookup inside a loop over all members
for name in wanted:
    data = archive.read(name)

# ✅ Single pass / indexed access according to archive API
members = {m.filename: m for m in archive.get_members()}
```

**Review questions:**
- [ ] What is the complexity in number of members? compressed bytes?
- [ ] Does behavior stay acceptable on multi-GB inputs / 100k+ members?
- [ ] Are copies (`bytes(bytearray)`, repeated `read()`) avoidable?

## Concurrency & Parallelism

Library API is **sync-first**. Treat new threads/processes as exceptional:

- [ ] No shared mutable archive state across threads without proof of safety
- [ ] Prefer sequential clarity over premature parallel extract
- [ ] If using `ProcessPoolExecutor` / threads in tools/benchmarks, isolate process state

## Performance Review Checklist

### 🔴 Must Check (Blocking)

- [ ] Silent O(n) re-decompression of solid data on common APIs
- [ ] Unbounded memory allocation from hostile sizes
- [ ] Regression vs stdlib-scale expectations on ZIP/TAR hot paths (when relevant)

### 🟡 Should Check (Important)

- [ ] Streaming available where extraction isn’t required
- [ ] Handles closed; no obvious leaks in new readers
- [ ] Benchmarks updated when claiming perf changes

### 🟢 Nice-to-have

- [ ] Fewer copies on hot paths
- [ ] Clearer cost-model docs for new access patterns
