# format-detection — unconfirmed-format delta

## ADDED Requirements

### Requirement: An unconfirmed format choice is reported when the listing is empty

`detected_by="extension"` means every content signal declined: magic, the content
probes, and far magic all failed, and the extension was the only evidence left. That is
the same answer content detection gives when it **refuses** a file — so a format chosen
this way is not confirmed by the bytes, and no second detection pass is needed to know
it.

On its own that is fine and common (an empty `.br` with the Brotli extra missing, a
`.tlz` whose content is unreadable). Combined with a **listing that completes with zero
members**, it is the realistic form of the wrong-format problem: 32 KiB of zeros named
`z.tar` opens as an empty TAR, while `detect_format()` on the same bytes raises
`FormatDetectionError`.

When a listing completes without error and with zero members, the system SHALL therefore
emit `EXTENSION_FORMAT_UNCONFIRMED` if the format came from the extension fallback, and
SHALL NOT emit it otherwise. It SHALL NOT refuse the open: a zero-member archive is legal
and a zero-filled file is byte-identical to an empty one (see `diagnostics`).

The check SHALL cost nothing on a non-empty listing — it is a comparison of the recorded
`FormatInfo`, not a rescan.

#### Scenario: unconfirmed format matrix

| Case | Expected |
| --- | --- |
| 32 KiB of zeros named `z.tar` | Opens as TAR, 0 members, `EXTENSION_FORMAT_UNCONFIRMED` (+ `EMPTY_ARCHIVE`) |
| `detect_format()` on the same bytes with no name | `FormatDetectionError` — unchanged |
| Real one-member tar named `a.tar`, no magic window match | Non-empty listing → no diagnostic |
| A legitimately empty tar (all zeros, hence extension-only) | `EXTENSION_FORMAT_UNCONFIRMED` too — the bytes genuinely did not confirm it, and that is the honest answer, not a false positive |
| Empty archive opened with an explicit `format=` | `EXPLICIT_FORMAT_LISTED_EMPTY` instead — no extension fallback ran |
