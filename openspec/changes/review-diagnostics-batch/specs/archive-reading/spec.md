# archive-reading — assertion-vs-resource rule delta

## MODIFIED Requirements

### Requirement: Opening an archive for reading

The system SHALL expose:

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    seekable_members: bool = False,
    concurrent_members: bool = False,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader
```

`source`, multi-volume ordering, `streaming`, password candidates/providers,
encoding, configuration precedence, and backend selection retain their existing
contracts. `format=None` auto-detects; an explicit format bypasses detection.

**An explicit argument the resolved backend cannot act on is handled by its
*intent*, and the rule SHALL be applied to every such argument:**

> **Refuse** when the argument is an **assertion about this archive**. **Permit, and
> record a diagnostic**, when it is a **resource offered for use if needed.**

| Argument | Intent | Behaviour when the backend cannot act on it |
| --- | --- | --- |
| `format=` | assertion — "I claim this is a ZIP" | refuse when it cannot hold (see the directory rule below) |
| `password=` | resource — a keyring | permit in **every** form; `PASSWORD_ARGUMENT_UNUSED` |
| `encoding=` | resource — a hint for name decoding | permit; `ENCODING_ARGUMENT_UNUSED` |

`password=` on a format with no encryption SHALL NOT raise, in any of its forms — a
single value, an ordered sequence, and a provider callable SHALL behave identically
(accepted, never consulted, one diagnostic). A *wrong* password on an *encrypted*
archive is unaffected and still raises. Each backend SHALL declare whether it consumes
`encoding` (`ReadBackend.USES_ENCODING`) the same way it declares
`ReadBackend.SUPPORTS_PASSWORD`, so the check is central rather than per-backend
silence.

A **directory path** resolves to `ArchiveFormat.DIRECTORY`. An explicit `format=`
naming anything else SHALL raise `ArchiveyUsageError` rather than being discarded:
silently overruling it returns a reader over the directory tree to a caller who
asserted a different format, so every read downstream succeeds on the wrong data.
`format=ArchiveFormat.DIRECTORY` and `format=None` both remain valid. This is the
assertion half of the rule above, not a special case.

**Diagnostics at open (observable):** On success, advisory events from automatic
detection (if any) appear in this reader's cumulative `diagnostics` for its
lifetime and are not duplicated. Explicit `format=` skips detection, so open
adds no detection diagnostics. Unused-argument diagnostics are emitted before the
reader is returned, so they are readable without listing anything. If open raises,
no reader is returned.

Handoff mechanics (one shared collector/budget, no copy/re-seed): see
`format-detection` and `diagnostics`.

#### Scenario: open matrix

| Case | Expected |
| --- | --- |
| Auto-detect succeeds | Detection events visible on `reader.diagnostics`; not duplicated |
| `format=ArchiveFormat.ZIP` succeeds | No detection diagnostics from open |
| Open raises | No reader returned |
| `password="secret"` | Returned reader uses that password for encrypted members |
| `password=` any form, format with no encryption | Opens; `PASSWORD_ARGUMENT_UNUSED`; no raise |
| `encoding=` on a backend that decodes names another way | Opens; `ENCODING_ARGUMENT_UNUSED`; names unchanged |
| Directory path, no `format=` | Opens as `DIRECTORY` |
| Directory path, `format=ArchiveFormat.DIRECTORY` | Opens as `DIRECTORY` |
| Directory path, `format=ArchiveFormat.ZIP` | `ArchiveyUsageError`, naming the path and the requested format |
