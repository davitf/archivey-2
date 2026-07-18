# Surface size & public/internal boundary

`archivey.__all__` currently has **90 names**. The headline question — is that the
right size to freeze? — answer: **no, freeze ~70**: demote 14, add 2–3 that are
already de-facto public, and fix a handful of coherence nits. All moves are free
pre-release (0.2.0 is the first public release; nothing external imports these yet).

## Demote (remove from top-level `__all__`, keep importable from their module)

| Names | Why | Migration cost |
|---|---|---|
| The 13 `*Context` classes (`ArchiveEofContext` … `SymlinkTargetContext`) | Callers *receive* them on `Diagnostic.context`; nobody constructs or imports them to use the library. They are the payload schema of an extensible subsystem — every future `DiagnosticCode` adds another top-level name forever if they stay. `archivey.diagnostics` already exists as a clean public module with its own `__all__`; point `isinstance` users there. Keep `Diagnostic`, `DiagnosticCode`, `DiagnosticSummary`, `DiagnosticPolicy`, `DiagnosticSeverity`, `DiagnosticDisposition`, `DiagnosticContext` (the union alias) top-level — those are the API. | Free (pre-release). One `docs/api.md` note: "context classes live in `archivey.diagnostics`". |
| `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` | A benchmark-derived tuning constant (`config.py:75`). Freezing it top-level makes a *threshold value* API. Nobody needs it to *use* AUTO; anyone tuning it is already reading `config.py`. | Free. Keep in `archivey.config` for the curious. |

Result: 90 → 76. (If the maintainer wants to go further, `CreateSystem` and
`CompressionMethod.properties` are the next-most-esoteric, but they are member-field
types and cheap to keep — not recommended to cut.)

## Add / fix exports (gaps found via the CLI and signatures)

| Name | Problem | Fix |
|---|---|---|
| `PasswordInput` | The declared type of `open_archive(password=)` / `extract(password=)` — the CLI imports it from `archivey.config` (`cli/list_cmd.py:12` etc.) and any typed caller must too, but it is **not in `__all__`**. A public signature's parameter type is public API. | Export it (alias next to `PasswordProvider`/`PasswordRequest`, which already are). |
| `OnDiagnostic` | Type of the public `ArchiveyConfig.on_diagnostic` field; in `archivey.diagnostics.__all__` but not the package's. | Export or document the `archivey.diagnostics` import path in `api.md`. |
| `MemberSelector` vs `MemberSelectorArg` | Two names for the same alias: `reader.py:27` defines public `MemberSelector` (used by `stream_members`), while `extract_all`'s signature uses internal `MemberSelectorArg` (`reader.py:148`) — the *same class* shows users two different spellings for the same concept in help()/docs. | Keep one public `MemberSelector`; make `extraction_types.MemberSelectorArg` a private alias of it (or delete). Free. |
| `archivey.core.__all__` lists `source_name` | An internal streamtools helper published by a public module's `__all__` but absent from the package surface — `from archivey.core import *` pulls it in. | Drop it from `core.__all__` (it's re-exported "for the single implementation", i.e. for internal callers — they can import from `internal.streams.streamtools`). |

## Documentation surface (`docs/api.md`) is missing load-bearing names

`api.md` promises "everything documented here is … listed in `__all__`" but documents
only ~40 of 90. Fine for exception subclasses (the tree renders under `ArchiveyError`),
**not** fine for:

- **`open_stream`** — one of the three entry points, absent entirely.
- `MemberStreams` — the capability-declaration flag shown in `usage.md` examples.
- `MemberSelector` / `MemberFilter` — parameter types of `stream_members`/`extract_all`.
- `ExtractionProgress` — the `on_progress` payload.
- `PasswordProvider` / `PasswordRequest` / `PasswordInput`.
- `detect_format`'s return types `FormatInfo` / `DetectionConfidence`, and the
  support-query cluster (`format_availability`, `list_supported_formats`,
  `list_known_formats`, `FormatSupport`, `FormatAvailability`, `MissingComponent`).

At a freeze, an exported-but-undocumented name is a name you're committing to without
having decided what it promises. Either document or demote each. Migration: doc-only.

## Naming coherence

- **`Policy` suffix**: `ExtractionPolicy` (trust level), `OverwritePolicy` (collision
  action), `DiagnosticPolicy` (disposition mapping) — three different shapes share the
  suffix, but each is genuinely "a policy the caller sets", and the CLI mapped all
  three onto flags without friction. `OnError` breaks the pattern but mirrors its
  keyword (`on_error=`) exactly, same as `on_progress`/`on_diagnostic`. Verdict:
  coherent enough; **do not rename** (churn without an ergonomic win).
- **Enums-vs-flags convention** is consistent: `Enum` for closed choices, `str`-valued
  enums where serialization matters (`DiagnosticCode`, formats), `Flag` only where
  combination is meaningful (`MemberStreams`). Good.
- **`ArchiveStream`** — public *as a type* (annotation/return), never
  caller-constructed, but its `__init__` (with `open_fn`, `translate`, `stamp`, …)
  renders in docs/api.md today. Recommend: keep the name public, hide the constructor
  from the rendered docs and state "returned by `open()`/`open_stream()`, not
  constructed". Alternatively re-export a Protocol; not worth it — docstring note
  suffices.
- **`ArchiveFormat` display name** — the CLI had to parse `repr()` to print "ZIP"
  (`cli/info_cmd.py:16-23`). Add a small public property (e.g. `label` or reuse
  `file_extension()`-style method `display_name()`) returning `"ZIP"`, `"TAR_GZ"`, or
  the constructed fallback. One property; the CLI helper collapses.
- **`WriteError`** — exported and documented in the tree, but nothing raises it until
  Phase 9 writing lands. **Decided (Q6): demote / remove from the 0.2.0 read-only
  surface** (and stop advertising `[7z-write]` until writing is real). Do not freeze
  writing leftovers into the first public release.

## Smallest surface that still serves the three use cases

For calibration, the three canonical jobs need **~32 names**:

- **Entry + reader**: `open_archive`, `open_stream`, `extract`, `ArchiveReader`,
  `ArchiveStream` (annotation only).
- **Data model**: `ArchiveMember`, `MemberType`, `ArchiveInfo`, `ArchiveFormat`
  (+`ContainerFormat`/`StreamFormat` as its fields), `MemberStreams`.
- **Dedupe/list+hash**: `member.hashes` (no extra names — a field), `CostReceipt`,
  `ListingCost`, `AccessCost`, `StreamCapability`.
- **Safe extract**: `ExtractionPolicy`, `OverwritePolicy`, `OnError`,
  `ExtractionReport`, `ExtractionResult`, `ExtractionStatus`, `ExtractionProgress`,
  `MemberSelector`, `MemberFilter`, `ExtractionLimits`.
- **Errors**: `ArchiveyError`, `ArchiveyUsageError` + the subclasses callers branch on
  (`EncryptionError`, `CorruptionError`, `TruncatedError`, `UnsupportedFormatError`,
  `ResourceLimitError` at minimum).
- **Config/password**: `ArchiveyConfig`, `PasswordInput`/`PasswordProvider`/`PasswordRequest`.

Everything else — diagnostics detail, format-support introspection, `ListingLimits`,
`AcceleratorMode`, `CompressionAlgorithm`/`Method`, `CreateSystem`, the full exception
tree — is legitimate second-ring API that earns its keep, which is why the
recommendation is ~70, not ~32. The point of the exercise: nothing in the 90 is
*mysterious*; the only names that fail the "would a user ever type this?" test are the
14 demotions above.
