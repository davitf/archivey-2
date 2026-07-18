# Architecture Review Guide

A guide for architecture design reviews on a pure Python archive library (no web UI, SQL DB, or HTTP API).

## SOLID Principles Checklist

### S - Single Responsibility Principle (SRP)

**What to check:**
- Does this class/module have only one reason to change?
- Do all methods serve the same purpose?
- Can you describe it in one sentence?

**Signals:**
```
⚠️ Names contain "And", "Manager", "Handler", "Processor"
⚠️ Class exceeds 200–300 lines
⚠️ More than 5–7 public methods
⚠️ Methods operate on unrelated data
```

**Review questions:**
- "What responsibilities does this have? Can it be split?"
- "If format X changes, which methods change? What about codec Y?"

### O - Open/Closed Principle (OCP)

**What to check:**
- Does adding a format/codec require editing core dispatch code?
- Can new behavior be added via registry, subclass, or composition?
- Are there large if/else or match chains over format types?

**Signals:**
```
⚠️ switch/match chains over format or codec types
⚠️ New backend requires editing unrelated modules
⚠️ isinstance checks scattered outside the registry
```

**Review questions:**
- "If we add a new archive format, which files change?"
- "Will this dispatch table keep growing?"

### L - Liskov Substitution Principle (LSP)

**What to check:**
- Can subclasses replace the base wherever it is used?
- Do subclasses weaken or change the base contract?
- Do subclasses raise unexpected exception types?

**Signals:**
```
⚠️ Explicit downcasts to concrete reader types
⚠️ Subclass methods raise NotImplementedError
⚠️ Callers must isinstance-check the backend
```

**Review questions:**
- "Can any ReadBackend substitute here without caller changes?"
- "Does this override honor the base reader contract?"

### I - Interface Segregation Principle (ISP)

**What to check:**
- Are protocols/ABCs small and focused?
- Are implementers forced to stub unused methods?
- Do callers depend on methods they never use?

**Signals:**
```
⚠️ Backend ABC has more than 5–7 methods
⚠️ Empty methods or NotImplementedError stubs
⚠️ Broad names (IManager, IService)
⚠️ Clients use only a subset of the interface
```

**Review questions:**
- "Does every backend use every method on this ABC?"
- "Can this be split (e.g. list vs stream vs write)?"

### D - Dependency Inversion Principle (DIP)

**What to check:**
- Do high-level modules depend on abstractions (protocols, registries)?
- Are concrete parsers/codecs injected or resolved via registry, not hard-imported?
- Are abstractions owned by the layer that uses them?

**Signals:**
```
⚠️ Public API imports internal parser modules directly
⚠️ Backend hard-codes a codec instead of using the codec registry
⚠️ Optional extras imported at module top level (breaks zero-dep core)
⚠️ Hard to unit-test without real archive files
```

**Review questions:**
- "Can dependencies be replaced with fakes in tests?"
- "If we swap a decompressor or optional extra, how many call sites change?"

---

## Architecture Anti-Pattern Identification

### Critical Anti-Patterns

| Anti-pattern | Signals | Impact |
|--------|----------|------|
| **Big Ball of Mud** | No module boundaries; any module imports any other | Hard to understand, modify, and test |
| **God Object** | One class knows every format and every codec | High coupling; hard to reuse |
| **Spaghetti Code** | Chaotic control flow, deep nesting | Hard to trace extraction paths |
| **Lava Flow** | Ancient code nobody touches; no tests | Debt accumulates |

### Design Anti-Patterns

| Anti-pattern | Signals | Recommendation |
|--------|----------|------|
| **Golden Hammer** | Same pattern for every format | Match the solution to the format |
| **Gas Factory** | Simple parse wrapped in layers of indirection | YAGNI—start simple |
| **Boat Anchor** | Unused code for "later" | Delete; add when needed |
| **Copy-Paste Programming** | Same header/codec logic in multiple backends | Extract shared helpers |

### Review Questions

```markdown
🔴 [blocking] "This module has 2000 lines; split by format or concern"
🟡 [important] "This logic is duplicated in 3 backends—extract a shared helper?"
💡 [suggestion] "This match chain could be a registry or Strategy for new formats"
```

---

## Coupling and Cohesion Assessment

### Coupling Types (best to worst)

| Type | Description | Example |
|------|------|------|
| **Message coupling** ✅ | Data via parameters | `decompress(data, method_id)` |
| **Data coupling** ✅ | Share simple structures | `process_entry(ArchiveMember)` |
| **Stamp coupling** ⚠️ | Pass large object, use one field | Pass full reader to read only `.path` |
| **Control coupling** ⚠️ | Flags change behavior | `parse(header, strict=False)` |
| **Common coupling** ❌ | Shared mutable globals | Module-level parser state |
| **Content coupling** ❌ | Reach into another module's internals | Import `_private` from another backend |

### Cohesion Types (best to worst)

| Type | Description | Quality |
|------|------|------|
| **Functional** | Single task | ✅ Best |
| **Sequential** | Output feeds next step | ✅ Good |
| **Communicational** | Same data | ⚠️ Acceptable |
| **Temporal** | Done at same time | ⚠️ Poor |
| **Logical** | Related but different jobs | ❌ Bad |
| **Coincidental** | No clear link | ❌ Worst |

### Metric Reference

```yaml
Coupling:
  CBO (Coupling Between Objects): good < 5, warning 5–10, danger > 10
  Ce (efferent): how many externals this depends on — good < 7
  Ca (afferent): how many depend on this — high = large blast radius

Cohesion:
  LCOM4: 1 ✅ | 2–3 ⚠️ | >3 split ❌
```

### Review Questions

- "How many modules does this depend on? Can that shrink?"
- "If we change this backend, what else breaks?"
- "Do all methods here operate on the same abstraction?"

---

## Layered Architecture Review

### Library Layer Model

```
┌─────────────────────────────────────┐
│   Frameworks & Drivers (outer)      │  stdlib IO, pathlib, optional deps
│   (unrar, py7zr, zstandard, …)      │  (lazy / extras-gated)
├─────────────────────────────────────┤
│   Codecs & streams                  │  decompressors, crypto, counting IO
├─────────────────────────────────────┤
│   Format backends & parsers         │  zip/tar/7z/rar readers, detection
├─────────────────────────────────────┤
│   Orchestration                     │  extract, open_archive, registry
├─────────────────────────────────────┤
│   Public API (inner, stable)        │  types, exceptions, config, __init__
└─────────────────────────────────────┘
          ↑ Dependencies point inward ↑
```

Outer layers implement details; inner layers define contracts. Parsers and optional imports stay below the public surface.

### Dependency Rule Check

**Core rule: source dependencies point inward; `internal/` is not public.**

```python
# ❌ Public module imports parser internals
# archivey/core.py
from archivey.internal.backends.sevenzip_parser import parse_header

# ✅ Public API uses registry / backend ABC; parser stays internal
# archivey/internal/registry.py
def get_reader(fmt: ArchiveFormat) -> ReadBackend: ...

# archivey/internal/backends/sevenzip_reader.py
class SevenZipReader(ReadBackend):
    def list_members(self) -> Iterator[ArchiveMember]: ...
```

### Review Checklist

**Layer boundaries:**
- [ ] Does the public API (`archivey/`, re-exports) import `internal/` parser details?
- [ ] Do format backends leak parser types into `types.py` or exceptions?
- [ ] Are optional extras imported only when needed (not at core import time)?
- [ ] Does orchestration (`core`, `reader`, extraction) embed format-specific parse logic?

**Separation of concerns:**
- [ ] Is format detection separate from read/extract paths?
- [ ] Are codecs registered, not duplicated per backend?
- [ ] Is config/limits centralized (`config.py`), not scattered in parsers?

### Review Questions

```markdown
🔴 [blocking] "Public API imports sevenzip_parser — move behind ReadBackend"
🟡 [important] "Extract path contains ZIP-specific logic — move to zip_reader"
💡 [suggestion] "Gate optional import behind registry availability check"
```

---

## Design Pattern Usage Assessment

### When to Use Design Patterns

| Pattern | Good fit | Poor fit |
|------|----------|------------|
| **Factory / Registry** | Select reader or codec by format at runtime | Single format, fixed pipeline |
| **Strategy** | Swappable decompressor or filter | One algorithm, never changes |
| **Template method** | Shared extract flow, format-specific steps | No shared skeleton |
| **Decorator** | Counting, verify, crypto wrapping streams | Fixed, non-composable IO |
| **Singleton** | Rare; prefer explicit config objects | Anything injectable |

### Over-Design Warning Signals

```
⚠️ Simple match replaced by Strategy + Factory + Registry
⚠️ Protocol with only one implementation
⚠️ Abstraction "for later" with no second backend
⚠️ Line count grows from pattern ceremony
```

### Review Principles

```markdown
✅ Solves real extensibility (new format, new codec)
❌ Pattern for its own sake; violates YAGNI
```

### Review Questions

- "What problem does this pattern solve?"
- "What breaks if we inline it?"
- "Is the abstraction worth the complexity?"

---

## Extensibility Assessment

### Extensibility Checklist

**Functional:**
- [ ] New format: register backend + detection, minimal core edits?
- [ ] Extension points: registry, hooks, `Protocol` backends?
- [ ] Limits and behavior driven by `ArchiveyConfig`, not hardcoded?

**Format & codec:**
- [ ] New codec behind `internal/streams/codecs` (or equivalent)?
- [ ] Optional deps reported via `FormatSupport` / `MissingComponent`?
- [ ] Parser changes isolated to one backend module?

**Performance (library-scoped):**
- [ ] Streaming path avoids loading whole archive into memory?
- [ ] Solid blocks / large members handled incrementally?
- [ ] Hot paths avoid redundant decompress or seek?

### Extension Point Design

```python
# ✅ Registry-backed extension
def register_backend(fmt: ArchiveFormat, cls: type[ReadBackend]) -> None: ...

# ❌ Hardcoded format dispatch in core
def open_archive(path: Path):
    if path.suffix == ".zip":
        return ZipReader(path)
    elif path.suffix == ".7z":
        return SevenZipReader(path)
    ...
```

### Review Questions

```markdown
💡 [suggestion] "New format should register via registry, not edit core.py"
🟡 [important] "Codec logic hardcoded in backend — use codec registry?"
📚 [learning] "ReadBackend protocol keeps list/stream/extract consistent"
```

---

## Code Structure Best Practices

### Directory Organization

**By capability / format (recommended for archivey-style libs):**
```
src/archivey/
├── __init__.py          # stable public re-exports
├── core.py              # open_archive, extract, detect_format
├── types.py             # ArchiveFormat, ArchiveMember, …
├── exceptions.py
├── config.py
├── reader.py            # ArchiveReader orchestration
└── internal/
    ├── registry.py      # backend + codec registration
    ├── detection.py
    ├── extraction.py
    ├── backends/
    │   ├── zip_reader.py
    │   ├── tar_reader.py
    │   └── sevenzip_reader.py
    └── streams/
        ├── codecs.py
        └── decompress.py
```

**By technical layer only (avoid as top-level split):**
```
src/archivey/
├── parsers/       # all formats mixed, hard to navigate
├── codecs/
├── readers/
└── utils/
```

Prefer `internal/` for non-public modules; keep `__init__.py` exports intentional.

### Naming Convention Check

| Type | Convention | Example |
|------|------|------|
| Modules | snake_case | `zip_reader.py`, `sevenzip_parser.py` |
| Classes | PascalCase | `ZipReader`, `ArchiveMember` |
| Functions | snake_case | `open_archive`, `detect_format` |
| Constants | UPPER_SNAKE_CASE | `MAX_HEADER_SIZE` |
| Private | leading `_` | `_parse_local_header` |

### File Size Guidelines

```yaml
single file: < 300 lines
single function: < 50 lines
single class: < 200 lines
parameters: < 4
nesting: < 4 levels
```

### Review Questions

```markdown
🟢 [nit] "500-line backend — split parser vs reader"
🟡 [important] "Parser lives in public package — move under internal/backends"
💡 [suggestion] "Rename process() to parse_central_directory()"
```

---

## Quick Reference Checklist

### 5-Minute Architecture Review

```markdown
□ Dependencies point inward (public → orchestration → backends → codecs)?
□ No circular imports (registry ↔ backends)?
□ Public API free of parser/optional-dep imports?
□ SOLID and obvious anti-patterns?
□ New formats pluggable via registry, not core edits?
```

### Red Flags

```markdown
🔴 God Object — single module > 1000 lines
🔴 Circular import — A → B → C → A
🔴 Public API imports internal parsers or optional deps at import time
🔴 Hardcoded paths, limits, or secrets
🔴 Backend-specific types in public exceptions or types
```

### Yellow Flags

```markdown
🟡 CBO > 10
🟡 More than 5 parameters
🟡 Nesting > 4
🟡 Duplicated block > 10 lines across backends
🟡 Protocol with only one implementation
```

---

## Recommended Tools

| Tool | Purpose |
|------|------|
| **vulture** | Dead code detection |
| **import-linter** | Layer / import contracts |
| **ruff** | Complexity, style |
| **pyrefly / ty** | Type and dependency clarity |
| **pytest + coverage** | Behaviour and hotspot gaps |

---

## References

- [Clean Architecture - Uncle Bob](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [SOLID Principles in Code Review - JetBrains](https://blog.jetbrains.com/upsource/2015/08/31/what-to-look-for-in-a-code-review-solid-principles-2/)
- [Software Architecture Anti-Patterns](https://medium.com/@christophnissle/anti-patterns-in-software-architecture-3c8970c9c4f5)
- [Coupling and Cohesion](https://www.geeksforgeeks.org/system-design/coupling-and-cohesion-in-system-design/)
- [Design Patterns - Refactoring Guru](https://refactoring.guru/design-patterns)
