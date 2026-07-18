# Async and Concurrency Patterns — Python Guide

> Python concurrency pitfalls, structured concurrency patterns, and review guidance.
> **Archivey context:** the public library API is **sync-first** (no `async def` in core
> entry points). Apply this guide when reviewing async in tests, benchmarks, tooling,
> or future optional async layers — not when suggesting async refactors of sync APIs.

## Table of Contents

- [Python Concurrency Models](#python-concurrency-models)
- [Common Pitfalls](#common-pitfalls)
- [Best Practices](#best-practices)
- [Review Checklist](#review-checklist)

---

## Python Concurrency Models

| Model | When to use | Watch out for |
|-------|-------------|---------------|
| **Sequential (default)** | Archive I/O, parsing, extraction | Hidden shared mutable state in long-lived objects |
| **`threading` + locks** | Blocking I/O with overlap; legacy integrations | GIL limits CPU parallelism; races on shared state |
| **`asyncio`** | Many concurrent I/O waits on one thread | Blocking the event loop; leaked tasks |
| **`concurrent.futures`** | Thread/process pools from sync code | Unbounded submits; process pickling overhead |
| **`multiprocessing`** | CPU-bound work that must bypass the GIL | Startup cost; IPC complexity |

### Choosing an approach

```
I/O-bound (network, sockets, many small waits):
  → asyncio in async code paths
  → threading or asyncio.to_thread() from sync callers

CPU-bound (compression, hashing, parsing hot loops):
  → multiprocessing or ProcessPoolExecutor
  → NOT plain threads for heavy CPU on CPython

Archivey library core:
  → prefer sequential, clear sync code
  → parallelize only in tools/benchmarks with explicit bounds and isolation
```

---

## Common Pitfalls

### Pitfall 1: Race condition

Multiple threads or tasks mutate shared state without synchronization.

```python
import threading

counter = 0
lock = threading.Lock()

# ❌ counter += 1 in a loop is not atomic under threads
def increment_unsafe() -> None:
    global counter
    for _ in range(100_000):
        counter += 1

# ✅ serialize access
def increment_safe() -> None:
    global counter
    for _ in range(100_000):
        with lock:
            counter += 1
```

Races also appear when mixing `run_in_executor`, threads, or shared mutable objects
across asyncio tasks.

### Pitfall 2: Deadlock

Tasks acquire locks in inconsistent order and wait forever.

```python
import threading

lock_a, lock_b = threading.Lock(), threading.Lock()

def worker1() -> None:
    with lock_a, lock_b: ...

def worker2() -> None:
    with lock_b, lock_a: ...  # ❌ opposite order → deadlock
```

**Mitigations:** fixed global lock order, avoid nested locks, `acquire(timeout=...)`,
smaller critical sections.

### Pitfall 3: Starvation

Slow consumers never catch up when producers run unbounded.

```python
# ❌ unbounded queue → memory growth; consumers starve
queue: asyncio.Queue[bytes] = asyncio.Queue()
```

**Mitigations:** `Queue(maxsize=N)`, semaphores, explicit concurrency caps.

### Pitfall 4: Task / thread leak

Background work is started but never joined or cancelled.

```python
import asyncio
import threading

# ❌ task outlives scope
async def process() -> None:
    asyncio.create_task(long_running())

# ❌ thread started, never joined
def leak() -> None:
    threading.Thread(target=blocking_work).start()
```

**Mitigations:** `TaskGroup`, `gather`, `with ThreadPoolExecutor(...)`, or
`task.cancel()` + `await task` on shutdown.

### Pitfall 5: Blocking the event loop

Sync I/O or CPU work inside `async def` stalls every coroutine on that loop.

```python
import asyncio

# ❌ blocks the event loop
async def digest_member(path: str, name: str) -> bytes:
    with archivey.open_archive(path) as arc:
        return arc.read(name)  # sync I/O on the loop

# ✅ offload sync archivey calls from async tools
async def digest_member(path: str, name: str) -> bytes:
    def _read() -> bytes:
        with archivey.open_archive(path) as arc:
            return arc.read(name)

    return await asyncio.to_thread(_read)
```

Prefer `ProcessPoolExecutor` over `to_thread` for CPU-heavy work when the GIL matters.

---

## Best Practices

### 1. Structured concurrency (`TaskGroup`)

Bind child lifetimes to the parent scope; failures cancel siblings (Python 3.11+).

```python
import asyncio

async def process_items(items: list[Item]) -> list[Result]:
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(process_item(i)) for i in items]
    return [t.result() for t in tasks]
```

Prefer `TaskGroup` over fire-and-forget `create_task`. In sync archivey code, the
analogue is `with ThreadPoolExecutor(...) as pool` and waiting on all futures.

### 2. Cancellation propagation

Use timeouts; re-raise `CancelledError` after cleanup.

```python
import asyncio

async def process_with_timeout(item: Item, seconds: float = 30.0) -> Result:
    async with asyncio.timeout(seconds):
        return await process(item)

async def worker() -> None:
    try:
        await long_running()
    except asyncio.CancelledError:
        await cleanup()
        raise
```

Propagate shutdown via `asyncio.Event` in tests/tools so subprocesses and handles close.

### 3. Backpressure and bounded concurrency

Cap in-flight work with semaphores and bounded queues.

```python
import asyncio

async def process_all(paths: list[str], max_concurrent: int = 10) -> list[Result]:
    sem = asyncio.Semaphore(max_concurrent)

    async def process_one(path: str) -> Result:
        async with sem:
            return await asyncio.to_thread(process_archive, path)

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(process_one(p)) for p in paths]
    return [t.result() for t in tasks]
```

Combine `TaskGroup` + `Semaphore` + `asyncio.timeout` for batch helpers in async
tests/tools:

```python
async def process_batch(
    items: list[Item],
    *,
    max_concurrent: int = 10,
    timeout_seconds: float = 30.0,
) -> list[Result]:
    sem = asyncio.Semaphore(max_concurrent)

    async def one(item: Item) -> Result:
        async with sem, asyncio.timeout(timeout_seconds):
            return await process(item)

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(one(i)) for i in items]
    return [t.result() for t in tasks]
```

In sync code, bound `executor.submit` calls or use a fixed worker pool.

## Review Checklist

### Scope (archivey)

- [ ] Sync public APIs stay sync; async limited to tools/tests/future extras
- [ ] No shared mutable `Archive` / reader state across threads without documented safety
- [ ] Blocking archive work not called inside `async def` without `to_thread`

### Lifetimes, correctness, and bounds

- [ ] Every `create_task`, thread, or executor submit has join/cancel/await path
- [ ] `TaskGroup` or `gather` instead of fire-and-forget tasks
- [ ] Shared state protected (`Lock`, `RLock`, or single-owner design); consistent lock order
- [ ] Cancellation/timeouts propagate; subprocesses and handles close on shutdown
- [ ] Concurrency capped (`Semaphore`, bounded queue, fixed pool); backpressure on pipelines
- [ ] CPU-bound work uses processes when the GIL matters; I/O uses async or threads
- [ ] Independent work not serialized by unnecessary `await`; locks not held across I/O
- [ ] Parallelism in benchmarks/tools justified and isolated from library defaults
