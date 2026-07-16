# Availability & error contract (Brief §D) + streaming/lifecycle (§C)

## `[crypto]` absent → clean typed error (verified good)

With `_crypto_available()` patched to `False` in both `streams.crypto` and `zip_aes` (the
`[core-only]` simulation), all three native AES entry points raise
`PackageNotInstalledError` — not a bare `ImportError`, not a misleading `CorruptionError`:

```
7z AES:     PackageNotInstalledError OK
WinZip AES: PackageNotInstalledError OK
RAR hdr:    PackageNotInstalledError OK
```

- **7z:** `_open_aes_stage` → `open_aes_decrypt_stream` → `get_crypto_backend()` raises
  before any cipher import (`crypto.py:119`).
- **WinZip AES:** `open_winzip_aes_member` and `_AesCtrLe.__init__` both check
  `_crypto_available()` up front (`zip_aes.py:221,101`).
- **RAR:** `_HeaderDecryptStream.__init__` → `open_aes_decrypt_stage` raises; the parser
  loops (`_parse_rar3`/`_parse_rar5`) re-raise `PackageNotInstalledError` **before** the
  catch-all that wraps other exceptions in `EncryptionError` (`rar_parser.py:824,1190`), so
  the availability error is not mistranslated into a wrong-password error. The KDF/PBKDF2 and
  the RAR5 `_check_rar5_password` are pure-Python (stdlib `hashlib`), so a wrong password on a
  crypto-less install still fails as `EncryptionError` (correct) and only the actual
  header *decrypt* demands the extra.

The KDF/verification glue, BLAKE2sp, and ZipCrypto are pure-Python (stdlib
`hashlib`/`zlib`), so they run in `[core-only]`; only the AES stages need the extra, matching
the packaging spec.

## `[all-lowest]` — `cryptography>=45` API floor (verified by inspection)

The only `cryptography` surface used is
`Cipher(algorithms.AES(key), modes.ECB()/modes.CBC(iv))` with
`.encryptor()/.decryptor()/.update()/.finalize()` (`crypto.py:69`, `zip_aes.py:106`). These
are long-stable APIs present for many major versions before 45.0 — nothing added in 45.x or
later is used, so the minimum-version leg is safe. (Not run live because it needs a
`--resolution lowest-direct` re-sync; the API set is small and unambiguous by reading.)

## F4 (Low, hardening) — password reaches `unrar` argv

> **Fixed in #127.** `open_unrar_p` passes bare `-p` and writes `password + "\n"` on the
> child's stdin (`-p-` when no password). Argv no longer carries the secret.

`rar_unrar.py:53`:

```python
def _password_arg(password):
    ...
    return "-p" + password        # -> subprocess.Popen([unrar, "p", ..., "-pSECRET", path])
```

The password is an argv element of the `unrar` child, so it is visible to any local user via
`ps auxww` / `/proc/<pid>/cmdline` for the process lifetime. This matches `rarfile`'s
behaviour. No password leaks into exception text, `repr`, or logs on any path I read:
`_UnrarOwnedStream.close` raises a static
`EncryptionError("Incorrect RAR password or encrypted member")`, and the RAR parser's
`EncryptionError(f"Failed to decrypt RAR3 headers: {exc}")` wraps a decrypt-time exception
that does not carry the password.

**Q/D9 resolved (UnRAR source) — F4 is fixable, not unavoidable:** unrar has no env-var and no
`-p@file` channel, **but** a bare `-p` (no value) makes it read the password from **stdin** when
stdin is redirected (`GetPasswordText → getwstr`), e.g. `printf '%s\n' "$pw" | unrar x -p
archive.rar`. That is non-interactive and keeps the secret out of `argv`/`/proc/<pid>/cmdline`.
v2 spawns `unrar p` with the *data* on stdout, so the child's stdin is free — this is directly
usable. (The only caveat the source notes is that stdin is shared with `-si`, which v2 does not
use.)

**Fix (applied in #127):** `open_unrar_p` appends bare `-p` and passes `password + "\n"`
via `stdin=subprocess.PIPE` (keep `-p-` for the no-password case).

## Streaming / seek / lifecycle (§C) — no issues found

- **Seek within an encrypted member:** WinZip AES and (by the same `ReadOnlyIOStream` base)
  the RAR header stream are non-seekable; a WinZip AES member refuses `seek` cleanly rather
  than returning wrong plaintext (see `verification.md` "what is actually fine"). 7z encrypted
  members reach the seekable decoder layer only through the codec *after* AES-CBC decrypt has
  been folded in as a forward-only stage, and `AesDecryptStream` (`crypto.py:132`) is itself
  forward-only (no `seek`), so a backward seek re-runs from the folder start via the decoder
  layer's `_reset_to_seek_point`, which re-creates the whole pipeline (including a fresh AES
  stage) from the pack origin — CBC IV chaining is re-established from the folder's stored IV,
  not resumed mid-stream. No stale-CTR/stale-IV path observed.
- **Key caches key on the right tuple.** `SevenZipKeyCache` keys on
  `(password, salt, cycles)` (`crypto.py:247`), so a different salt/IV never reuses a key it
  was not derived for. The RAR3 per-archive `_Rar3EncState` cache (`rar_parser.py:963`) keys on
  the 8-byte salt and only reuses `(key, iv)` when the salt is identical. Both are per-reader
  instances; no cross-archive reuse. (Concurrency of the caches under CONCURRENT is Brief 3 /
  concurrency territory; the dicts are plain and not lock-guarded, but 7z/RAR key derivation is
  deterministic so a race only recomputes, never mixes keys.)
- **Truncated ciphertext raises.** `WinZipAesDecryptStream._pull` raises
  `CorruptionError("Truncated WinZip AES ciphertext before HMAC")` / `"Truncated WinZip AES
  HMAC"` on a short read (`zip_aes.py:158,166`); `AesDecryptStream` finalizes and the 7z folder
  length check raises `TruncatedError` (`sevenzip_pipeline.py:380`). No unauthenticated tail
  bytes escape on truncation for WinZip AES.
