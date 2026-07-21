# Tasks — verification integrity mode (STREAMING default + STRICT opt-in)

> Depends on `gzip-zlib-truncation-recovery` (read-path verdicts;
> `finish_on_close` teardown-only). Decisions/naming still open — see
> `design.md` Open Questions (confirm Q1 names and Q2 landing spot before coding).

## 1. Default-consistency (small; completes the never-raise-on-close contract)

- [ ] 1.1 Remove the authenticate-on-`close` drain from
      `WinZipAesDecryptStream.close` (`zip_aes.py`): `close` closes the source and
      wrapper, never drains ciphertext / verifies the HMAC. The HMAC still fires on
      a full read (when `_pull` consumes the MAC). Result: default (`STREAMING`)
      encrypted members never surface a content fault from `close`.
- [ ] 1.2 Confirm the fused `ArchiveStream` + `MemberVerifier` path and the plain
      decode path close a partially-read encrypted member quietly, matching CRC
      members (parity with the `gzip-zlib-truncation-recovery` close contract).
- [ ] 1.3 Tests: encrypted member — full read with a bad HMAC raises
      `CorruptionError` on the completing read; partial read then `close` is quiet;
      a wrong-password fast-fail (pre-stream) still raises at open, unchanged.

## 2. `VerificationMode` config surface

- [ ] 2.1 Add `VerificationMode` (`STREAMING` default, `STRICT`) and
      `ArchiveyConfig.verification_mode` (per Q1 naming). Thread it to
      `MemberVerifier` / the fused stream construction.
- [ ] 2.2 `STREAMING` keeps today's behavior (verify-as-you-go; abandon = no
      verdict; close never a first content fault) — assert no observable change
      except 1.x.

## 3. `STRICT` mode

- [ ] 3.1 Partial read in STRICT: force a bounded full verifying pass (honoring
      `extraction_limits` / output caps) before the stream is considered done;
      raise `CorruptionError` / `TruncatedError` on a corrupt/tampered/short member.
- [ ] 3.2 Seek in STRICT that would disable frontier verification: force a full
      verifying pass first, or fail the seek with a typed error (Q3 — pick per
      seekable source + limits). Never silently drop the check.
- [ ] 3.3 `close()` in STRICT after a partial read: complete verification
      (drain + verdict), uniformly for digest and auth-tag members.
- [ ] 3.4 STRICT verify-ahead must not become a bomb: cap by `extraction_limits`;
      over-cap raises rather than slurping unbounded.

## 4. Tests

- [ ] 4.1 Mode matrix (spec): STREAMING vs STRICT × {full read, partial+close,
      seek+read} × {good, corrupt, short} × {CRC/digest, encrypted}.
- [ ] 4.2 Parity: a digest member and an encrypted member behave identically for a
      given mode.
- [ ] 4.3 STRICT bomb bound: a highly compressible corrupt member verify-ahead
      stops at the cap.
- [ ] 4.4 Three dependency configs where crypto/extras affect the path
      (`[all]`, `[all-lowest]`, `[core-only]`).

## 5. Docs

- [ ] 5.1 `costs`: STRICT breaks the ≤~1.3× budget (full decode/decrypt ahead of
      use); STREAMING is the budgeted default.
- [ ] 5.2 `safe-extraction`: when to choose STRICT (untrusted archives / mandatory
      authentication); note STREAMING returns unauthenticated bytes on a partial
      read of an encrypted member.
- [ ] 5.3 `usage`: the `verification_mode` knob; decision note recording the AES
      close-drain removal and the mode rationale.

## 6. Verify

- [ ] 6.1 Targeted pytest for §§1–4; `pyrefly check` + `ty check`; `ruff`.
- [ ] 6.2 `openspec validate --strict verification-integrity-mode`.
