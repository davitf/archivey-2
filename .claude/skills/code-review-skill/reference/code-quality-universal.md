# Universal Code Quality Anti-Patterns

> Language-agnostic guide to code quality anti-patterns, covering code reuse, abstraction leaks, parameter bloat, nested conditions, string typing, TOCTOU, no-op updates, and other core topics. Applies to PR reviews in any language.

## Table of Contents

- [Code Reuse Review](#code-reuse-review)
- [Parameter Bloat](#parameter-bloat)
- [Abstraction Leaks](#abstraction-leaks)
- [String Typing](#string-typing)
- [Nested Conditional Expressions](#nested-conditional-expressions)
- [Copy-Paste Variants](#copy-paste-variants)
- [No-Op Updates](#no-op-updates)
- [TOCTOU Race Conditions](#toctou-race-conditions)
- [Overly Broad Operations](#overly-broad-operations)
- [Redundant State](#redundant-state)
- [Universal Quality Review Checklist](#universal-quality-review-checklist)

---

## Code Reuse Review

Before accepting new code, search the existing codebase for reusable utilities.

### Search for Existing Utility Functions

```python
# ❌ New path-joining logic—the project already has safe_join
def member_dest(root: Path, name: str) -> Path:
    return root / name.replace("..", "")

# ✅ Use the existing path helper
def member_dest(root: Path, name: str) -> Path:
    return safe_join(root, name)
```

```python
# ❌ Hand-rolled CRC32—the project already has archivey._util.crc32
def zip_crc(data: bytes) -> int:
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF

# ✅ Use the existing utility
from archivey._util import crc32
```

**Review points:**
- Does the new function duplicate or overlap with an existing utility by name or behavior?
- Can inline logic be replaced with a call to an existing module?
- Check adjacent files and the shared/utils directory

---

## Parameter Bloat

### Function Parameters Keep Growing

```python
# ❌ Add a parameter for every new requirement
def extract_member(archive, name, dest, password, overwrite, preserve_mtime, strip_components):
    ...

# ✅ Use a configuration object / dataclass
@dataclass
class ExtractOptions:
    dest: Path
    password: str | None = None
    overwrite: bool = False
    preserve_mtime: bool = True
    strip_components: int = 0

def extract_member(archive: Archive, name: str, options: ExtractOptions) -> Path:
    ...
```

**Review points:**
- Does the function have ≥ 4 parameters? Consider an options object / dataclass
- Is the new parameter just a boolean flag? Consider an enum or strategy pattern
- Are there mutually exclusive parameters like `enable_x`, `disable_y`?

---

## Abstraction Leaks

### Exposing Internal Implementation Details

```python
# ❌ Returns raw ZIP central-directory records—callers must know zipfile internals
def list_members(archive_path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(archive_path) as zf:
        return zf.infolist()

# ✅ Return domain objects; hide the format backend
def list_members(archive_path: Path) -> list[ArchiveMember]:
    with open_archive(archive_path) as archive:
        return list(archive.members())
```

**Review points:**
- Does the return type leak the underlying implementation (parser structs, codec handles, file format)?
- Does the function depend on a backend's internal data structures?
- Does it break existing abstraction boundaries?

---

## String Typing

### Using Raw Strings Instead of Constants/Enums

```python
# ❌ Magic strings scattered everywhere
if format_name == "zip":
    ...
if compression == "deflate":
    ...

# ✅ Use enums
class ArchiveFormat(StrEnum):
    ZIP = "zip"
    TAR = "tar"
    SEVEN_Z = "7z"

if detected == ArchiveFormat.ZIP:
    ...
```

**Review points:**
- Are strings used where an existing enum/union type should be?
- Are format names, member kinds, and codec IDs scattered across multiple files?
- Are string comparisons case-sensitive without validation?

---

## Nested Conditional Expressions

### Ternary Chains and Nested if/else

```python
# ❌ Ternary chain is hard to read
label = (
    "ZIP" if fmt == "zip" else
    "TAR" if fmt == "tar" else
    "7z" if fmt == "7z" else
    "Unknown"
)

# ✅ Lookup table or match
FORMAT_LABELS = {
  ArchiveFormat.ZIP: "ZIP",
  ArchiveFormat.TAR: "TAR",
  ArchiveFormat.SEVEN_Z: "7z",
}
label = FORMAT_LABELS.get(fmt, "Unknown")
```

```python
# ❌ Nested if 3+ levels deep
def extract_tree(archive, dest):
    if archive is not None:
        if archive.members:
            for member in archive.members:
                if member.is_file:
                    ...

# ✅ Early return + guard clauses
def extract_tree(archive, dest):
    if archive is None or not archive.members:
        return
    for member in archive.members:
        if not member.is_file:
            continue
        ...
```

**Review points:**
- Are ternary expressions nested ≥ 2 levels deep?
- Is if/else nesting ≥ 3 levels deep?
- Can this be replaced with a lookup table, early return, or match?

---

## Copy-Paste Variants

### Nearly Identical Code Blocks

```python
# ❌ Two parsers differ only in magic bytes and header layout
def sniff_zip(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(4) == b"PK\x03\x04"

def sniff_tar(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(262)[257:262] == b"ustar"

# ✅ Unified signature check
def matches_signature(path: Path, offset: int, magic: bytes) -> bool:
    with path.open("rb") as f:
        f.seek(offset)
        return f.read(len(magic)) == magic
```

**Review points:**
- Are there ≥ 2 code blocks that differ only in variable names, offsets, or magic bytes?
- Can a parameterized shared function be extracted?
- Can template method or strategy eliminate the variants?

---

## No-Op Updates

### Unconditionally Triggering Updates

```python
# ❌ Rewrite the index file on every member visit—even when metadata is unchanged
for member in archive.members():
    member.mtime = normalize_mtime(member.mtime)
    index.write(member)

# ✅ Update only when the value changes
for member in archive.members():
    new_mtime = normalize_mtime(member.mtime)
    if member.mtime != new_mtime:
        member.mtime = new_mtime
        index.write(member)
```

**Review points:**
- Do polling / interval / event handlers update unconditionally?
- Does the wrapper function respect same-reference return?
- Do filesystem or index writes check for actual changes?

---

## TOCTOU Race Conditions

### Time-of-Check-to-Time-of-Use

```python
# ❌ Check then operate—the file may be deleted/created in between
if os.path.exists(path):
    with open(path) as f:
        data = f.read()

# ✅ Operate directly + handle exceptions
try:
    with open(path) as f:
        data = f.read()
except FileNotFoundError:
    data = None
```

```python
# ❌ Check member exists → extract is not atomic if the archive changes
if archive.has_member(name):
    archive.extract(name, dest)

# ✅ Extract directly + handle missing/changed members
try:
    archive.extract(name, dest)
except MemberNotFoundError:
    ...
except ArchiveChangedError:
    ...
```

**Review points:**
- Can the `if exists → operate` pattern be replaced with `try operate → catch`?
- Are multi-step state changes inside a transaction/lock?
- Is there an await between check and act in async code?

---

## Overly Broad Operations

### Reading Too Much Data

```python
# ❌ Read the entire archive to get one member's header
data = path.read_bytes()
offset = find_central_directory(data)
member = parse_central_directory_record(data, offset)

# ✅ Seek to the header; don't load the whole file
with path.open("rb") as f:
    f.seek(central_dir_offset)
    member = parse_central_directory_record(f)
```

```python
# ❌ List every member to find one by name
members = list(archive.members())
target = next(m for m in members if m.name == name)

# ✅ Indexed lookup
target = archive.get_member(name)
```

**Review points:**
- Is an entire collection/file read when only a small part is used?
- Can filtering be pushed to the parser/index layer?
- Does the API support seek/limit instead of full enumeration?

---

## Redundant State

### State That Can Be Derived

```python
# ❌ Cached values can become stale when source data changes
class ArchiveListing:
    member_count: int       # redundant if len(members) gives the same
    total_uncompressed: int # redundant if sum(m.size for m in members)
    members: list[ArchiveMember]

# ✅ Derive or use a property
class ArchiveListing:
    members: list[ArchiveMember]

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def total_uncompressed(self) -> int:
        return sum(m.uncompressed_size for m in self.members)
```

**Review points:**
- Are there fields that can be derived from other fields?
- Do cached values have an invalidation mechanism?
- Can observer/effect be replaced with a direct call?

---

## Universal Quality Review Checklist

- [ ] **Reuse review**: Searched for existing utilities/helpers—no reinventing the wheel?
- [ ] **Parameter count**: Function has ≤ 3 parameters? If more, use an options object / dataclass?
- [ ] **Abstraction boundaries**: Return types don't expose internal implementation details (parser structs, codec handles, file format)?
- [ ] **Type safety**: No magic strings where an existing enum/constant/union type should be used?
- [ ] **Conditional depth**: Ternary nesting ≤ 1 level? if/else nesting ≤ 2 levels?
- [ ] **DRY**: No copy-paste-with-variation (≥ 2 near-identical blocks)?
- [ ] **No-op guards**: Polling / interval / event handlers have change-detection guards?
- [ ] **TOCTOU**: `if exists → operate` replaced with `try operate → catch`?
- [ ] **Data precision**: Not reading an entire collection/file just to take a subset?
- [ ] **Redundant state**: No stored fields that can be derived from other fields?
