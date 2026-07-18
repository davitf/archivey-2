# Python Code Review Guide

> Python code review guide covering type annotations, async/await, testing, exception handling, performance optimization, and other core topics.

## Table of Contents

- [Type Annotations](#type-annotations)
- [Async and Concurrency](#async-and-concurrency)
- [Exception Handling](#exception-handling)
- [Common Pitfalls](#common-pitfalls)
- [Testing Best Practices](#testing-best-practices)
- [Performance Optimization](#performance-optimization)
- [Code Style](#code-style)
- [Review Checklist](#review-checklist)

---

## Type Annotations

### Basic Type Annotations

```python
# ❌ No type annotations — the IDE cannot help
def process_data(data, count):
    return data[:count]

# ✅ Use type annotations
def process_data(data: str, count: int) -> str:
    return data[:count]

# ✅ Use the typing module for complex types
from typing import Optional, Union

def find_member(archive: Archive, name: str) -> Member | None:
    """Return the member or None."""
    for member in archive.list_members():
        if member.name == name:
            return member
    return None

def handle_input(value: Union[str, int]) -> str:
    """Accept a string or integer."""
    return str(value)
```

### Container Type Annotations

```python
from typing import List, Dict, Set, Tuple, Sequence

# ❌ Imprecise types
def get_names(users: list) -> list:
    return [u.name for u in users]

# ✅ Precise container types (Python 3.9+ can use list[User] directly)
def get_names(users: List[User]) -> List[str]:
    return [u.name for u in users]

# ✅ Use Sequence for read-only sequences (more flexible)
def process_items(items: Sequence[str]) -> int:
    return len(items)

# ✅ Dictionary types
def count_words(text: str) -> Dict[str, int]:
    words: Dict[str, int] = {}
    for word in text.split():
        words[word] = words.get(word, 0) + 1
    return words

# ✅ Tuple (fixed length and types)
def get_point() -> Tuple[float, float]:
    return (1.0, 2.0)

# ✅ Variable-length tuple
def get_scores() -> Tuple[int, ...]:
    return (90, 85, 92, 88)
```

### Generics and TypeVar

```python
from typing import TypeVar, Generic, List, Callable

T = TypeVar('T')
K = TypeVar('K')
V = TypeVar('V')

# ✅ Generic function
def first(items: List[T]) -> T | None:
    return items[0] if items else None

# ✅ Bounded TypeVar
from typing import Hashable
H = TypeVar('H', bound=Hashable)

def dedupe(items: List[H]) -> List[H]:
    return list(set(items))

# ✅ Generic class
class Cache(Generic[K, V]):
    def __init__(self) -> None:
        self._data: Dict[K, V] = {}

    def get(self, key: K) -> V | None:
        return self._data.get(key)

    def set(self, key: K, value: V) -> None:
        self._data[key] = value
```

### Callable and Callbacks

```python
from pathlib import Path
from typing import Callable, Awaitable

# ✅ Function type annotation
Handler = Callable[[str, int], bool]

def register_handler(name: str, handler: Handler) -> None:
    handlers[name] = handler

# ✅ Async callback (tests/tooling only — library API is sync-first)
AsyncReader = Callable[[Path], Awaitable[bytes]]

async def read_with_handler(
    path: Path,
    handler: AsyncReader,
) -> bytes:
    return await handler(path)

# ✅ Function that returns a function
def create_multiplier(factor: int) -> Callable[[int], int]:
    def multiplier(x: int) -> int:
        return x * factor
    return multiplier
```

### TypedDict and Structured Data

```python
from typing import TypedDict, Required, NotRequired

# ✅ Define dictionary structure
class UserDict(TypedDict):
    id: int
    name: str
    email: str
    age: NotRequired[int]  # Python 3.11+

def create_user(data: UserDict) -> User:
    return User(**data)

# ✅ Partially required fields
class ConfigDict(TypedDict, total=False):
    debug: bool
    timeout: int
    host: Required[str]  # This field is required
```

### Protocol and Structural Subtyping

```python
from typing import Protocol, runtime_checkable

# ✅ Define a protocol (type checking for duck typing)
class Readable(Protocol):
    def read(self, size: int = -1) -> bytes: ...

class Closeable(Protocol):
    def close(self) -> None: ...

# Compose protocols
class ReadableCloseable(Readable, Closeable, Protocol):
    pass

def process_stream(stream: Readable) -> bytes:
    return stream.read()

# ✅ Runtime-checkable protocol
@runtime_checkable
class Drawable(Protocol):
    def draw(self) -> None: ...

def render(obj: object) -> None:
    if isinstance(obj, Drawable):  # Runtime check
        obj.draw()
```

---

## Async and Concurrency

> **Archivey is sync-first.** Public entry points (`open_archive`, `extract`, format
> readers) are synchronous. Do not suggest turning core library code async unless an
> explicit change proposal covers it.

> For deeper Python concurrency review (threading, pools, asyncio patterns, review
> checklists), see [Async and Concurrency Cross-Language Guide](cross-cutting/async-concurrency-patterns.md).

When async or threads appear in **tests, benchmarks, or tooling**, watch for these
high-value pitfalls:

### Blocking the event loop

```python
import asyncio
import time
import zipfile
from pathlib import Path

# ❌ Sync archive I/O inside async code blocks the loop
async def bad_extract(archive: Path, dest: Path) -> None:
    time.sleep(0.1)  # Blocks every other coroutine on this loop
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)

# ✅ Offload blocking work to a thread (or keep the helper sync)
async def good_extract(archive: Path, dest: Path) -> None:
    def _extract() -> None:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)

    await asyncio.to_thread(_extract)
```

### Missing await

```python
from pathlib import Path

async def list_member_names(archive: Path) -> list[str]:
    ...

# ❌ Returns a coroutine object — names are never collected
async def bad_list(archive: Path) -> list[str]:
    return list_member_names(archive)

# ✅ Await the coroutine
async def good_list(archive: Path) -> list[str]:
    return await list_member_names(archive)
```

### TaskGroup basics (Python 3.11+)

```python
import asyncio
from pathlib import Path

async def checksum_member(root: Path, name: str) -> tuple[str, int]:
    ...

async def checksum_all(root: Path, names: list[str]) -> list[tuple[str, int]]:
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(checksum_member(root, n)) for n in names]
    # All tasks finished; first failure cancels siblings and raises ExceptionGroup
    return [t.result() for t in tasks]
```

For `asyncio.gather`, cancellation, semaphores, and sync/async boundaries, use the
cross-cutting guide linked above.

---

## Exception Handling

> 📖 For general principles and cross-language examples, see [Error Handling Cross-Language Guide](cross-cutting/error-handling-principles.md)

### Exception Handling Best Practices

```python
# ❌ Catching too broad
try:
    result = risky_operation()
except:  # Catches everything, even KeyboardInterrupt!
    pass

# ❌ Catching Exception without handling it
try:
    result = risky_operation()
except Exception:
    pass  # Swallows all exceptions, making debugging hard

# ✅ Catch specific exceptions
try:
    result = risky_operation()
except ValueError as e:
    logger.error(f"Invalid value: {e}")
    raise
except IOError as e:
    logger.error(f"IO error: {e}")
    return default_value

# ✅ Multiple exception types
try:
    result = parse_and_process(data)
except (ValueError, TypeError, KeyError) as e:
    logger.error(f"Data error: {e}")
    raise DataProcessingError(str(e)) from e
```

### Exception Chaining

```python
# ❌ Losing the original exception information
try:
    members = reader.list_members()
except FormatError as e:
    raise ArchiveError("read failed")  # Loses the cause

# ✅ Use from to preserve the exception chain
try:
    members = reader.list_members()
except FormatError as e:
    raise ArchiveError("read failed") from e

# ✅ Explicitly break the exception chain (rare)
try:
    members = reader.list_members()
except FormatError:
    raise ArchiveError("read failed") from None
```

### Custom Exceptions

```python
# ✅ Define a business exception hierarchy
class AppError(Exception):
    """Base application exception."""
    pass

class ValidationError(AppError):
    """Data validation error."""
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")

class NotFoundError(AppError):
    """Resource not found."""
    def __init__(self, resource: str, id: str | int):
        self.resource = resource
        self.id = id
        super().__init__(f"{resource} with id {id} not found")

# Usage
def get_member(archive: Archive, name: str) -> Member:
    member = find_member(archive, name)
    if member is None:
        raise NotFoundError("member", name)
    return member
```

### Exceptions in Context Managers

```python
from contextlib import contextmanager

# ✅ Handle exceptions correctly in context managers
@contextmanager
def staged_extract(archive: Archive, dest: Path):
    staging = dest / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        yield staging
        archive.extract_all(staging)
        staging.rename(dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

# ✅ Use ExceptionGroup (Python 3.11+)
def process_batch(items: list) -> None:
    errors = []
    for item in items:
        try:
            process(item)
        except Exception as e:
            errors.append(e)

    if errors:
        raise ExceptionGroup("Batch processing failed", errors)
```

---

## Common Pitfalls

### Mutable Default Arguments

```python
# ❌ Mutable default arguments
def add_item(item, items=[]):  # Bug! Shared across calls
    items.append(item)
    return items

# Demonstration of the problem
add_item(1)  # [1]
add_item(2)  # [1, 2] instead of [2]!

# ✅ Use None as default
def add_item(item, items=None):
    if items is None:
        items = []
    items.append(item)
    return items

# ✅ Or use dataclass field
from dataclasses import dataclass, field

@dataclass
class Container:
    items: list = field(default_factory=list)
```

### Mutable Class Attributes

```python
# ❌ Using mutable class attributes
class User:
    permissions = []  # Shared across all instances!

# Demonstration of the problem
u1 = User()
u2 = User()
u1.permissions.append("admin")
print(u2.permissions)  # ["admin"] — unexpectedly shared!

# ✅ Initialize in __init__
class User:
    def __init__(self):
        self.permissions = []

# ✅ Use dataclass
@dataclass
class User:
    permissions: list = field(default_factory=list)
```

### Closures in Loops

```python
# ❌ Closure captures the loop variable
funcs = []
for i in range(3):
    funcs.append(lambda: i)

print([f() for f in funcs])  # [2, 2, 2] instead of [0, 1, 2]!

# ✅ Capture the value with a default argument
funcs = []
for i in range(3):
    funcs.append(lambda i=i: i)

print([f() for f in funcs])  # [0, 1, 2]

# ✅ Use functools.partial
from functools import partial

funcs = [partial(lambda x: x, i) for i in range(3)]
```

### is vs ==

```python
# ❌ Using is to compare values
if x is 1000:  # May not work!
    pass

# Python caches small integers (-5 to 256)
a = 256
b = 256
a is b  # True

a = 257
b = 257
a is b  # False!

# ✅ Use == to compare values
if x == 1000:
    pass

# ✅ Use is only for None and singletons
if x is None:
    pass

if x is True:  # Strict boolean check
    pass
```

### String Concatenation Performance

```python
# ❌ Concatenating strings in a loop
result = ""
for item in large_list:
    result += str(item)  # O(n²) complexity

# ✅ Use join
result = "".join(str(item) for item in large_list)  # O(n)

# ✅ Use StringIO to build large strings
from io import StringIO

buffer = StringIO()
for item in large_list:
    buffer.write(str(item))
result = buffer.getvalue()
```

---

## Testing Best Practices

### pytest Basics

```python
import pytest

# ✅ Clear test names
def test_user_creation_with_valid_email():
    user = User(email="test@example.com")
    assert user.email == "test@example.com"

def test_user_creation_with_invalid_email_raises_error():
    with pytest.raises(ValidationError):
        User(email="invalid")

# ✅ Use parameterized tests
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("World", "WORLD"),
    ("", ""),
    ("123", "123"),
])
def test_uppercase(input: str, expected: str):
    assert input.upper() == expected

# ✅ Test exceptions
def test_division_by_zero():
    with pytest.raises(ZeroDivisionError) as exc_info:
        1 / 0
    assert "division by zero" in str(exc_info.value)
```

### Fixtures

```python
import pytest
from pathlib import Path
from typing import AsyncIterator, Generator

# ✅ Basic fixture
@pytest.fixture
def user() -> User:
    return User(name="Test User", email="test@example.com")

def test_user_name(user: User):
    assert user.name == "Test User"

# ✅ Fixture with cleanup
@pytest.fixture
def temp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace
    # tmp_path teardown is automatic; add explicit cleanup only when needed

# ✅ Async fixture (tests/tooling only — library API is sync-first)
@pytest.fixture
async def async_extract_dir(tmp_path: Path) -> AsyncIterator[Path]:
    dest = tmp_path / "extracted"
    dest.mkdir()
    yield dest

# ✅ Shared fixtures (conftest.py)
# conftest.py
@pytest.fixture(scope="session")
def fixture_root() -> Path:
    """Fixture directory shared across the entire test session."""
    return Path("tests/fixtures")

@pytest.fixture(scope="module")
def sample_zip(fixture_root: Path) -> Path:
    """Sample archive path shared per test module."""
    return fixture_root / "sample.zip"
```

### Mock and Patch

```python
from unittest.mock import Mock, patch, AsyncMock

# ✅ Mock external dependencies
def test_extract_member():
    mock_reader = Mock()
    mock_reader.read_member.return_value = b"payload"

    result = extract_member(mock_reader, "data.txt")

    assert result == b"payload"
    mock_reader.read_member.assert_called_once_with("data.txt")

# ✅ Patch module-level functions
@patch("archivey.formats.zip._read_central_directory")
def test_with_patched_reader(mock_read):
    mock_read.return_value = [Member(name="a.txt")]

    members = list_members(Path("fixture.zip"))

    assert [m.name for m in members] == ["a.txt"]

# ✅ Async mock (tests/tooling only)
async def test_async_list_members():
    mock_list = AsyncMock(return_value=["a.txt", "b.txt"])

    with patch("tools.bench.async_list_members", mock_list):
        result = await list_members_async(Path("fixture.zip"))

    assert result == ["a.txt", "b.txt"]
```

### Test Organization

```python
# ✅ Use classes to organize related tests
class TestZipExtraction:
    """Tests for ZIP extraction behaviour."""

    def test_extracts_regular_file(self, sample_zip: Path, tmp_path: Path):
        dest = tmp_path / "out"
        extract(sample_zip, dest)
        assert (dest / "hello.txt").read_text() == "hello\n"

    def test_rejects_path_traversal(self, malicious_zip: Path, tmp_path: Path):
        with pytest.raises(SecurityError):
            extract(malicious_zip, tmp_path / "out")

    def test_lists_members_without_extracting(self, sample_zip: Path):
        with open_archive(sample_zip) as archive:
            names = [m.name for m in archive.list_members()]
        assert "hello.txt" in names

# ✅ Use marks to tag tests
@pytest.mark.slow
def test_large_data_processing():
    pass

@pytest.mark.integration
def test_round_trip_extract(tmp_path: Path, sample_zip: Path):
    pass

# Run specific marks: pytest -m "not slow"
```

### Coverage and Quality

```python
# pytest.ini or pyproject.toml
[tool.pytest.ini_options]
addopts = "--cov=archivey --cov-report=term-missing --cov-fail-under=80"
testpaths = ["tests"]

# ✅ Test edge cases
def test_empty_input():
    assert process([]) == []

def test_none_input():
    with pytest.raises(TypeError):
        process(None)

def test_large_input():
    large_data = list(range(100000))
    result = process(large_data)
    assert len(result) == 100000
```

---

## Performance Optimization

### Choosing Data Structures

```python
# ❌ List lookup O(n)
if item in large_list:  # Slow
    pass

# ✅ Set lookup O(1)
large_set = set(large_list)
if item in large_set:  # Fast
    pass

# ✅ Use the collections module
from collections import Counter, defaultdict, deque

# Counting
word_counts = Counter(words)
most_common = word_counts.most_common(10)

# Default dictionary
graph = defaultdict(list)
graph[node].append(neighbor)

# Deque (O(1) operations at both ends)
queue = deque()
queue.appendleft(item)  # O(1) vs list.insert(0, item) O(n)
```

### Generators and Iterators

```python
from pathlib import Path

# ❌ Load all member metadata at once
def get_all_member_names(archive: Path) -> list[str]:
    with open_archive(archive) as ar:
        return [m.name for m in ar.list_members()]  # High memory if huge

# ✅ Stream members with a generator
def iter_member_names(archive: Path):
    with open_archive(archive) as ar:
        for member in ar.list_members():
            yield member.name  # Lazy iteration

# ✅ Generator expression
sum_of_squares = sum(x**2 for x in range(1000000))  # Does not create a list

# ✅ itertools module
from itertools import islice, chain, groupby

# Take only the first 10
first_10 = list(islice(infinite_generator(), 10))

# Chain multiple iterators
all_items = chain(list1, list2, list3)

# Grouping
for key, group in groupby(sorted(items, key=get_key), key=get_key):
    process_group(key, list(group))
```

### Caching

```python
from functools import lru_cache, cache

# ✅ LRU cache
@lru_cache(maxsize=128)
def expensive_computation(n: int) -> int:
    return sum(i**2 for i in range(n))

# ✅ Unbounded cache (Python 3.9+)
@cache
def fibonacci(n: int) -> int:
    if n < 2:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

# ✅ Manual cache (when you need more control)
class DataService:
    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._cache_ttl: dict[str, float] = {}

    def get_data(self, key: str) -> Any:
        if key in self._cache:
            if time.time() < self._cache_ttl[key]:
                return self._cache[key]

        data = self._fetch_data(key)
        self._cache[key] = data
        self._cache_ttl[key] = time.time() + 300  # 5 minutes
        return data
```

### Parallel Processing

```python
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path

# ✅ Use a thread pool for I/O-bound archive work
def extract_all(archives: list[Path], dest: Path) -> list[Path]:
    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(lambda p: extract_one(p, dest), archives))

# ✅ Use a process pool for CPU-bound work
def checksum_members(data: list[bytes]) -> list[int]:
    with ProcessPoolExecutor() as executor:
        return list(executor.map(hash_bytes, data))

# ✅ Use as_completed to handle results as they finish
from concurrent.futures import as_completed

with ThreadPoolExecutor() as executor:
    futures = {executor.submit(extract_one, path, dest): path for path in archives}
    for future in as_completed(futures):
        path = futures[future]
        try:
            result = future.result()
        except Exception as e:
            print(f"{path} failed: {e}")
```

---

## Code Style

### PEP 8 Essentials

```python
# ✅ Naming conventions
class MyClass:  # Class names: PascalCase
    MAX_SIZE = 100  # Constants: UPPER_SNAKE_CASE

    def method_name(self):  # Methods: snake_case
        local_var = 1  # Variables: snake_case

# ✅ Import order
# 1. Standard library
import os
import sys
from typing import Optional

# 2. Third-party packages
import numpy as np
import pandas as pd

# 3. Local modules
from archivey import open_archive
from archivey.extract import extract

# ✅ Line length limit (79 or 88 characters)
# Wrapping long expressions
result = (
    long_function_name(arg1, arg2, arg3)
    + another_long_function(arg4, arg5)
)

# ✅ Blank line rules
class MyClass:
    """Class docstring."""

    def method_one(self):
        pass

    def method_two(self):  # One blank line between methods
        pass


def top_level_function():  # Two blank lines between top-level definitions
    pass
```

### Docstrings

```python
# ✅ Google-style docstring
def calculate_area(width: float, height: float) -> float:
    """Calculate the area of a rectangle.

    Args:
        width: Width of the rectangle (must be positive).
        height: Height of the rectangle (must be positive).

    Returns:
        The area of the rectangle.

    Raises:
        ValueError: If width or height is negative.

    Example:
        >>> calculate_area(3, 4)
        12.0
    """
    if width < 0 or height < 0:
        raise ValueError("Dimensions must be positive")
    return width * height

# ✅ Class docstring
class DataProcessor:
    """Utility class for processing and transforming data.

    Attributes:
        source: Path to the data source.
        format: Output format ('json' or 'csv').

    Example:
        >>> processor = DataProcessor("data.csv")
        >>> processor.process()
    """
```

### Modern Python Features

```python
# ✅ f-string (Python 3.6+)
name = "World"
print(f"Hello, {name}!")

# With expressions
print(f"Result: {1 + 2 = }")  # "Result: 1 + 2 = 3"

# ✅ Walrus operator (Python 3.8+)
if (n := len(items)) > 10:
    print(f"List has {n} items")

# ✅ Positional-only and keyword-only parameters (Python 3.8+)
def greet(name, /, greeting="Hello", *, punctuation="!"):
    """name is positional-only; punctuation is keyword-only."""
    return f"{greeting}, {name}{punctuation}"

# ✅ Pattern matching (Python 3.10+)
def handle_member(member: Member):
    match member:
        case Member(name=name, is_dir=True):
            return list_children(name)
        case Member(name=name, compressed_size=0):
            raise EmptyMemberError(name)
        case Member(name=name):
            return read_member(name)
```

---

## Review Checklist

### Type Safety
- [ ] Functions have type annotations (parameters and return values)
- [ ] Use `Optional` to make `None` explicit
- [ ] Generic types are used correctly
- [ ] mypy checks pass (no errors)
- [ ] Avoid `Any`; add comments when it is necessary

### Async and Concurrency (when present)
- [ ] Core library APIs stay sync; async belongs in tests/tooling unless explicitly scoped
- [ ] `async`/`await` are paired; coroutines are awaited, not returned by mistake
- [ ] No blocking sync I/O or `time.sleep` on the asyncio event loop
- [ ] Structured concurrency uses `TaskGroup` or `asyncio.gather` with clear error propagation
- [ ] Broader threading/pool guidance: `cross-cutting/async-concurrency-patterns.md`

### Exception Handling
- [ ] Catch specific exception types; do not use bare `except:`
- [ ] Preserve causes with `from` in exception chains
- [ ] Custom exceptions inherit from appropriate base classes
- [ ] Exception messages are meaningful and aid debugging

### Data Structures
- [ ] No mutable default arguments (list, dict, set)
- [ ] Class attributes are not mutable objects
- [ ] Correct data structures are chosen (set vs list lookup)
- [ ] Generators are used instead of lists for large datasets

### Testing
- [ ] Test coverage meets the target (recommended ≥80%)
- [ ] Test names clearly describe the scenario
- [ ] Edge cases are covered
- [ ] Mocks correctly isolate external dependencies
- [ ] Async test helpers have corresponding `pytest.mark.asyncio` (or equivalent) tests

### Code Style
- [ ] Follow the PEP 8 style guide
- [ ] Functions and classes have docstrings
- [ ] Import order is correct (stdlib, third-party, local)
- [ ] Naming is consistent and meaningful
- [ ] Modern Python features are used (f-strings, walrus operator, etc.)

### Performance
- [ ] Avoid creating objects repeatedly in loops
- [ ] Use join for string concatenation
- [ ] Use caching appropriately (`@lru_cache`)
- [ ] Use the right parallelism for I/O-bound vs CPU-bound work
