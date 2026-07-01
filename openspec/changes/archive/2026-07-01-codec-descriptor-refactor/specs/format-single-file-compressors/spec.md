# Single-File Compressor Format Behavior — delta (codec-descriptor refactor)

## MODIFIED Requirements

### Requirement: Per-codec metadata comes from the codec descriptor

The single multi-format `SingleFileBackend` SHALL obtain each format's metadata extraction
from its codec descriptor's metadata hook rather than a reader-local dispatch table, keeping
the reader codec-agnostic. The "one backend, per-codec hooks" structure SHALL be preserved —
only the hooks' home moves onto the descriptor — and the surfaced metadata (gzip `FNAME` →
`extra["gzip.original_filename"]` + `raw_name`, gzip mtime, xz/lzip decompressed size, and
the per-format size-availability rules) MUST be unchanged.

#### Scenario: gzip metadata extraction lives on the codec descriptor

- **WHEN** a `.gz` source with a stored `FNAME` and mtime is opened
- **THEN** `extra["gzip.original_filename"]` (Latin-1 decoded), `raw_name`, and `modified` are populated exactly as before, via the gzip descriptor's metadata hook rather than a reader method

#### Scenario: a codec with no extra metadata needs no hook

- **WHEN** a `.bz2` source (no header metadata) is opened
- **THEN** the member carries the default shell with `size` `None`, because its descriptor registers no metadata hook
