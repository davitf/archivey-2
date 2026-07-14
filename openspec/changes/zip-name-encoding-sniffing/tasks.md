## 1. Config

- [x] 1.1 Add a legacy-fallback encoding to `ArchiveyConfig`
      (`zip_unflagged_fallback_encoding`, default `"cp437"`).

## 2. ZIP backend decode

- [x] 2.1 In `zip_reader.py`, decode unflagged member names from the recovered raw bytes:
      UTF-8 first (`_sniff_unflagged_name`), else the configured legacy fallback.
- [x] 2.2 Gate the sniff: skip when bit 11 is set (decode UTF-8) or when the caller passed an
      explicit `encoding=` (used verbatim by `zipfile`'s `metadata_encoding`).
- [x] 2.3 Emit a `MEMBER_NAME_ENCODING_INFERRED` diagnostic (member + chosen encoding) when
      UTF-8 (or a non-cp437 fallback) overrides the cp437 default; new `NameEncodingContext`.
- [x] 2.4 No bare `UnicodeDecodeError` escapes — the fallback (cp437 by default) covers all bytes.

## 3. Tests

- [x] 3.1 Vendored `encoding_infozip_jules.zip` under `tests/fixtures/external/`; asserts names
      decode to `Español.txt`, `Català.txt`, `Português.txt`, `emoji_😀.txt` (`tests/test_zip.py`).
- [x] 3.2 Gate coverage: flag set → UTF-8; explicit `encoding=` → verbatim (no sniff, no
      diagnostic); invalid-UTF-8 → configured/cp437 fallback; diagnostic only when overriding.
- [x] 3.3 Diagnostic is escalatable to `DiagnosticRaisedError` under a `DiagnosticPolicy`.

## 4. Docs

- [x] 4.1 Document unflagged-name behavior, the `encoding=` override, and the configurable
      fallback in `docs/formats.md` (ZIP section).
