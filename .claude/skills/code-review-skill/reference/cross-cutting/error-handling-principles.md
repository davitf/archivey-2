# Error Handling Principles — Python Library Guide

> Principles, anti-patterns, hierarchy design, and logging for a **pure Python library**.
> Callers own logging and user-facing messages; the library surfaces typed exceptions
> with enough context to act on them.

## Table of Contents

- [Core Principles](#core-principles)
- [Anti-Patterns](#anti-patterns)
- [Error Hierarchy Design](#error-hierarchy-design)
- [Exception Translation](#exception-translation)
- [Logging in a Library](#logging-in-a-library)
- [Python Patterns](#python-patterns)
- [Review Checklist](#review-checklist)

---

## Core Principles

### 1. Do Not Swallow Errors

Propagate, translate to a library type, or degrade with an explicit documented fallback.
**Never** ignore silently. Bare `except:` and `except Exception: pass` are almost always
wrong; if you catch broadly for cleanup, re-raise or chain.

```python
# ❌ Silent failure
try:
    header = read_header(stream)
except OSError:
    return None

# ✅ Translate and chain
try:
    header = read_header(stream)
except OSError as exc:
    raise TruncatedError("unexpected EOF while reading header") from exc
```

### 2. Add Context

Name the **operation** and include **key parameters** (path, member, offset, codec).
Prefer structured attributes (`archive_name`, `member_name`) on the exception.

```python
raise CorruptionError(
    f"CRC32 mismatch for {name!r} (expected 0x{exp:08x}, got 0x{act:08x})",
    archive_name=path, member_name=name,
)
```

### 3. Use Specific Types

Let callers branch (`except EncryptionError` vs `except CorruptionError`). One root for
archive problems (`ArchiveyError`); a **separate** base for API misuse
(`ArchiveyUsageError`) so `except ArchiveyError` does not mask caller bugs.

### 4. Fail Fast

Validate preconditions before expensive I/O or decompression — not after reading
megabytes.

### 5. Handle Each Error Once

Translate at the format boundary; do not log, wrap, and re-raise at every layer. The
application decides whether to log or retry.

---

## Anti-Patterns

### Empty `except` Blocks

```python
# ❌ Swallows KeyboardInterrupt
try:
    result = parse_header(stream)
except:
    pass

# ✅ Specific mapping or propagate
try:
    result = parse_header(stream)
except struct.error as exc:
    raise CorruptionError("malformed header") from exc
```

### Overly Broad `except`

```python
# ❌ Masks bugs
try:
    member = reader.open_member(name)
except Exception as exc:
    raise ReadError(str(exc)) from exc

# ✅ Known failures only
try:
    member = reader.open_member(name)
except KeyError as exc:
    raise ReadError(f"member {name!r} not found") from exc
except zlib.error as exc:
    raise CorruptionError("decompression failed") from exc
```

Never convert *any* `Exception` to a library type.

### Losing the Original Exception

```python
# ❌ Cause lost
except OSError:
    raise TruncatedError("unexpected EOF")

# ✅ Chain preserved
except OSError as exc:
    raise TruncatedError("unexpected EOF while reading member data") from exc
```

Use `raise NewError("…") from exc`. Inside `except`, bare `raise` keeps the traceback.

### Exceptions for Control Flow

```python
# ❌
try: codec = CODECS[codec_id]
except KeyError: codec = default_codec

# ✅
codec = CODECS.get(codec_id, default_codec)
```

Reserve exceptions for corrupt data, missing members, wrong passwords — not routine
lookups (except `StopIteration` in iterators).

### Ignoring Return Values

```python
n = stream.readinto(buf)
if n < len(buf):
    raise TruncatedError(f"expected {len(buf)} bytes, got {n}")
```

Check `Optional` returns and partial reads explicitly.

---

## Error Hierarchy Design

```
┌─────────────────────────────────────────────────────────────┐
│ Caller — catches ArchiveyError; owns logging / retries / UI   │
├─────────────────────────────────────────────────────────────┤
│ Public: ArchiveyError → OpenError, ReadError, ExtractionError│
│         ArchiveyUsageError (misuse — NOT under ArchiveyError)│
├─────────────────────────────────────────────────────────────┤
│ Internal: OSError, struct.error, zlib.error — translate here  │
└─────────────────────────────────────────────────────────────┘
```

**Rules**

1. `except ArchiveyError` is the supported catch-all for archive failures.
2. Usage errors (`ArchiveyUsageError`, or `TypeError`/`ValueError` for bad args) stay
   outside that tree.
3. Translate known stdlib/third-party exceptions at reader boundaries; never leak raw
   `zlib.error` from public APIs.
4. Carry context as attributes, not only in the message.
5. Let `KeyboardInterrupt`, `SystemExit`, and usually bare caller `OSError` propagate
   unless a spec says otherwise (e.g. per-member `OSError` under `OnError.CONTINUE`).

```python
class ArchiveyError(Exception):
    def __init__(self, message: str, *, archive_name: str | None = None,
                 member_name: str | None = None) -> None:
        super().__init__(message)
        self.archive_name = archive_name
        self.member_name = member_name

class ReadError(ArchiveyError): ...
class CorruptionError(ReadError): ...
class EncryptionError(ReadError): ...
class ArchiveyUsageError(Exception): ...
```

Subclass by **failure domain** (open / read / extract / limits), not by format.

---

## Exception Translation

Map *known* failures; return `None` for anything unrecognized so it surfaces in tests.

```python
def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
    if isinstance(exc, lzma.LZMAError):
        return CorruptionError("invalid LZMA stream", archive_name=self.path)
    if isinstance(exc, struct.error):
        return CorruptionError("malformed structure", archive_name=self.path)
    return None

def read_central_directory(self) -> list[MemberInfo]:
    try:
        return self._parse_central_directory()
    except Exception as exc:
        if (t := self._translate_exception(exc)) is not None:
            raise t from exc
        raise
```

- One `isinstance` branch per known mode — no blanket `except Exception: raise ArchiveyError`.
- Always `raise translated from exc`.
- Exercise every mapped path with corrupt, truncated, encrypted, and wrong-password fixtures.

---

## Logging in a Library

**Quiet by default.** Use `logging` (never `print`); let callers log caught `ArchiveyError`.

| Level | Library use |
|-------|-------------|
| DEBUG | Optional diagnostics (offsets, probes) |
| WARNING | Recoverable anomaly caller might miss |
| ERROR | Rare — prefer raising |
| INFO | Leave to the application |

```python
logger = logging.getLogger(__name__)
try:
    return _read_trailing(stream)
except OSError as exc:
    logger.debug("trailing probe failed: %s", exc)
    return 0  # only when the API contract defines this sentinel
```

- Never log passwords, keys, or member contents.
- Parameterized messages: `logger.debug("offset %d", off)`.
- **Raise, don't only log** — unless the API explicitly returns a sentinel.

---

## Python Patterns

**Translate with context**

```python
def read_member_data(self, name: str) -> bytes:
    try:
        return self._decompress(self._read_raw(name))
    except KeyError as exc:
        raise ReadError(f"member {name!r} not found",
                        archive_name=self.path, member_name=name) from exc
    except zlib.error as exc:
        raise CorruptionError(f"deflate error for {name!r}",
                              archive_name=self.path, member_name=name) from exc
```

**Cleanup without swallowing** — `except BaseException` only when cleanup must run for
`KeyboardInterrupt`; always bare `raise` afterward.

```python
try:
    tmp = self._write_temp(dest); tmp.replace(dest)
except BaseException:
    if tmp is not None: tmp.unlink(missing_ok=True)
    raise
```

**`ExceptionGroup` (3.11+)** — when closing multiple streams, collect errors rather than
dropping secondary failures.

**Context managers** — do not return `True` from `__exit__` to suppress unless documented.
Document `raises` on public methods; translate before errors cross the public boundary.

---

## Review Checklist

### Core

- [ ] No empty `except:` / `except Exception: pass`
- [ ] No catch-all mapping every `Exception` to a library type
- [ ] Messages name operation + key parameters; specific types used
- [ ] Chains preserved (`from exc` / bare `raise`); fail fast; one handler per layer

### Hierarchy and Translation

- [ ] `ArchiveyError` vs `ArchiveyUsageError` separation
- [ ] Specific boundary translations; unrecognized exceptions propagate
- [ ] `KeyboardInterrupt` / `SystemExit` not converted
- [ ] Context attributes on exceptions; error-path tests present

### Logging and Idioms

- [ ] No `print`; sparse `logging`; no secrets in logs
- [ ] Failures raised, not silently logged away
- [ ] Specific `except` targets; no exception control flow; partial reads checked
