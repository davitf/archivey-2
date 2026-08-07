# `required_source` on `FormatAvailability`

## Why

Whether a format can be read from a pipe is **behaviour that already exists and is not
queryable**. TAR and the single-file compressors stream from a non-seekable source; ZIP,
ISO, 7z and RAR keep their index at the end and refuse one with `StreamNotSeekableError`.
The refusal is loud, typed and uniform — the simplicity & consistency review's F8
confirmed that half is fine — but a caller writing "pipe it if you can, otherwise buffer
to disk" has to *try it and catch the exception*. The project's own rule is that
behaviour differences between formats are surfaced as data, never discovered by trial.

`FormatAvailability` is a **public frozen dataclass**, so its field set effectively
freezes at the `0.2.0` tag. This is the only item in that review with a release deadline.

## What Changes

- **`backend-registry`** — `FormatAvailability` gains
  `required_source: StreamCapability`, *the weakest source shape this format can read
  from*, derived from the backend's existing `SUPPORTS_STREAMING_NON_SEEKABLE` declaration
  (so there is one fact, declared once, in one place):

  | Format | `required_source` |
  |---|---|
  | TAR (incl. compressed combos), single-file compressors | `FORWARD_ONLY` |
  | ZIP, ISO, 7z, RAR, directory | `SEEKABLE` |

- **`access-mode-and-cost`** — `StreamCapability` becomes **ordered**
  (`FORWARD_ONLY < SEEKABLE`), so `required_source` reads as a minimum and the caller's
  test is a comparison against the same type the cost receipt already publishes:

  ```python
  if format_availability(fmt).required_source <= reader.cost.stream_capability:
      ...  # this source is strong enough for this format
  ```

Not changing: the refusal itself, its error type, or which formats accept a pipe. This
change only makes the existing split queryable.

## Impact

- Specs: `backend-registry`, `access-mode-and-cost`.
- Code: `src/archivey/cost.py` (ordering), `src/archivey/internal/registry.py` (the field).
- Docs: `docs/opening-and-listing.md` (the pipe paragraph gains the queryable answer),
  `docs/access-and-cost.md` (the ordering).
- Public API: additive — a new field with a conservative default, and comparison
  operators on an enum that had none. No existing behaviour changes.
