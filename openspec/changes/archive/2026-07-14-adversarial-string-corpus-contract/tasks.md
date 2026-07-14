## 1. Corpus construction

- [x] 1.1 Generate deterministic clean ZIP/TAR bases in memory and remove committed generated binaries.
- [x] 1.2 Mutate the exact ZIP/TAR fields, including both ZIP UTF-8 flags and both symlink-data CRC fields.
- [x] 1.3 Assert each case's stored bytes and exact decoded/read/extraction semantics with honest labels.

## 2. Runtime behavior

- [x] 2.1 Emit one bidi-control warning from central member registration for every backend.
- [x] 2.2 Reject NUL-bearing link targets with `SymlinkEscapeError` before filesystem
      resolution. (Landed on main via `hypothesis-property-tests`; this branch merged
      main and dropped its narrower duplicate guard.)
- [x] 2.3 Cover central warning behavior through ZIP/TAR, directory, and single-file readers.
- [x] 2.4 Translate a filesystem's write-time refusal of a representable member name
      (`EILSEQ` from UTF-8-enforcing filesystems) into a typed `ExtractionError`.
      Landed on main via `hypothesis-property-tests`; this branch's corpus
      `filesystem_name_refusal` case now requires the typed error (no raw
      `OSError` arm).

## 3. Documentation and verification

- [x] 3.1 Reconcile `ARCHITECTURE.md` with generated-on-demand adversarial archives and exceptional committed fixtures.
- [x] 3.2 Validate the OpenSpec change and run focused/full quality gates.
