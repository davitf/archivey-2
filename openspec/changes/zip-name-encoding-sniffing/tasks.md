## 1. Config

- [ ] 1.1 Add a legacy-fallback encoding to `ArchiveyConfig` (default `"cp437"`); confirm the
      final field name against existing config naming (working: `zip_unflagged_fallback_encoding`).

## 2. ZIP backend decode

- [ ] 2.1 In `zip_reader.py`, decode unflagged member names from raw bytes: UTF-8 first, then
      the configured legacy fallback. Do not rely on `zipfile`'s cp437 default for this path.
- [ ] 2.2 Gate the sniff: skip entirely when bit 11 is set (decode UTF-8) or when the caller
      passed an explicit `encoding=` (use it verbatim).
- [ ] 2.3 Emit a `diagnostics` warning (member + chosen encoding) when UTF-8 is inferred for
      an unflagged name; wire it through `DiagnosticPolicy` like other backend diagnostics.
- [ ] 2.4 Confirm no bare `UnicodeDecodeError` can escape name decoding (fallback covers all bytes).

## 3. Tests

- [ ] 3.1 Port `encoding_infozip_jules.zip` into `tests/fixtures/` as a regression fixture;
      assert names decode to `Español.txt`, `Català.txt`, `Português.txt`, `emoji_😀.txt`.
- [ ] 3.2 Unit-cover the gate: flag set → UTF-8; explicit `encoding=` → used verbatim (no sniff);
      invalid-UTF-8 bytes → legacy fallback; and the override diagnostic is emitted only when expected.
- [ ] 3.3 Assert a diagnostic is raised (not just logged) under an escalating `DiagnosticPolicy`.

## 4. Docs

- [ ] 4.1 Document the unflagged-name behavior and the `encoding=` override in `docs/formats.md`
      (ZIP section); note the configurable legacy fallback.
