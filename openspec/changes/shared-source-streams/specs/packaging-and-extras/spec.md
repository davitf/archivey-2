# Packaging and Extras — delta (shared-source-streams)

## MODIFIED Requirements

### Requirement: Supported Runtime Environment

The system SHALL declare and support Python 3.11 or newer on Linux, macOS, and
Windows. The public API is synchronous only for v1, and readers and writers are
not thread-safe (one per thread). As the one carve-out, member streams already opened
from a single reader MAY be read from different threads without data corruption
(reads are serialized by the shared-source lock, not parallelized); the reader object
itself — `open()`, iteration, `close()` — MUST NOT be driven from multiple threads.

#### Scenario: install rejected on unsupported Python

- **WHEN** installation is attempted on a Python interpreter older than 3.11
- **THEN** the `requires-python` constraint (`>=3.11`) prevents installation

#### Scenario: supported on all three operating systems

- **WHEN** the library is installed on Linux, macOS, or Windows under Python 3.11+
- **THEN** the core and any installed optional formats are supported on that platform

#### Scenario: one reader per thread, open streams excepted

- **WHEN** a caller uses a single reader from one thread but reads its already-opened member
  streams from other threads
- **THEN** the data is correct (serialized by the shared-source lock); driving the reader
  object itself (concurrent `open()`, iteration, or `close()`) from multiple threads remains
  unsupported
