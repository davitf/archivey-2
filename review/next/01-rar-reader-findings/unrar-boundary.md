# `unrar` subprocess boundary (`rar_unrar.py`, `rar_reader.py`)

`unrar` is **not installed** in this review container, so the member-*data* paths
could not be exercised end-to-end. Findings here are from argv construction (shown
live in `repro.py` F3) and code tracing of the exit-code / stream-length handling.
Both should be re-confirmed against a real RARLAB `unrar` before fixing.

---

## F3 (Med) — hostile member names reach the `unrar` argv unescaped

`open_unrar_p` builds the command by appending the archive path and the member name
positionally, with **no `--` end-of-switches separator**:

```python
# rar_unrar.py:78-84
cmd = [unrar, "p", "-inul"]
if version_control:
    cmd.append("-ver")
cmd.append(_password_arg(password))
cmd.append(str(archive_path))
if member is not None:
    cmd.append(member)          # <-- attacker-controlled, unescaped
```

The `member` value comes from `_presented_filename(raw)` (`rar_reader.py:574-576`) —
deliberately the *raw parser filename*, not the normalized `member.name` — so a
member name from a hostile header reaches `unrar` verbatim. `unrar` scans its whole
argv for switches (leading `-`) and treats a file argument beginning with `@` as a
list-file. Demonstrated argv (`repro.py` F3):

```
member='-inul'        -> argv tail ['-p-', 'archive.rar', '-inul']
member='@/etc/passwd' -> argv tail ['-p-', 'archive.rar', '@/etc/passwd']
member='-p-secret'    -> argv tail ['-p-', 'archive.rar', '-p-secret']
```

Concrete consequences from a crafted archive:

- **`@`-prefixed name → local file read.** A member named `@somepath` makes `unrar`
  read `somepath` from disk (relative to CWD) as a newline-separated list of member
  names to extract. The untrusted archive thus influences `unrar` into opening an
  attacker-chosen local path. Under `p -inul` this does not write to disk, but it is
  an unintended local-file access driven by archive contents.
- **`-`-prefixed name → switch injection.** A member named `-x…`, `-inul`, `-p-…`,
  etc. is parsed as a switch. Best case the target member is simply not matched;
  combined with F4 (below) that becomes a silent empty stream rather than an error.

This is precisely the axis the spec's **"Constrain unrar argv by call site"**
requirement exists to protect (`openspec/specs/format-rar/spec.md`: *"MUST NOT pass
… globs, or `@listfile` filters"*). The current code honours that for the argv *the
backend intends to build*, but not for the case where the hostile **member name
itself** is a switch/`@listfile`. The spec's own constraint is therefore only
half-enforced.

**Suggested fix.** Insert `"--"` before the archive path so nothing after it is
parsed as a switch, and reject (or otherwise neutralize) member names beginning with
`@` before they reach the argv — `--` stops switch parsing but does **not** stop
`unrar`'s `@`-listfile expansion of a file argument, so the two need separate
handling. Verify the chosen mitigation against a real `unrar` (not testable here).

---

## F4 (Med) — incomplete `unrar` exit-code mapping + unchecked stream length

Only exit code 11 (bad password) is translated; every other non-zero `unrar` code is
dropped:

```python
# rar_reader.py:159-164 (_UnrarOwnedStream.close)
rc = self._proc.returncode
super(DelegatingStream, self).close()
# unrar exit 11 = bad password (RARLAB).
if rc == 11:
    raise EncryptionError("Incorrect RAR password or encrypted member")
# rc in (2 fatal, 3 CRC/corrupt, 10 no files matched, ...) -> silently ignored
```

Separately, the nonsolid single-member path wraps stdout in a `SlicingStream` with a
declared `length=size` but **never verifies** `unrar` actually produced `size` bytes:

```python
# rar_reader.py:578-583
owned = self._track_decompressed(_UnrarOwnedStream(stdout, proc))
size = _member_stream_size(member)
sliced = SlicingStream(owned, length=size, own_source=True)
return self._wrap_payload_stream(sliced, member, track_output=False)
```

`SlicingStream.read` clamps to available bytes and returns short at EOF without
error. So if `unrar` emits fewer than `size` bytes — because the member is corrupt
(rc 3), a fatal error occurred (rc 2), or the name didn't match (rc 10, e.g. the F3
switch-injection case) — the reader hands back a **silently truncated or empty**
stream and swallows the non-11 exit code.

**Mitigation that already exists:** members carrying a CRC32 or BLAKE2sp hash are
wrapped in `VerifyingStream` (`rar_reader.py:509-516`), which raises on a hash
mismatch when the stream is read to completion — so most *real* corruption on a
fully-read member is still caught. The gap is:

- members with **no** stored hash (CRC-less, or CRC suppressed by
  `_crc_is_tweaked` for tweaked-checksum encrypted members — `rar_reader.py:112-125`);
- callers that read partially and close (CRC never checked);
- the empty-output / no-match case, which reads cleanly to a (zero-length) EOF.

The **solid** path is better off: `SolidBlockReader.open_member` raises `EOFError`
when the pipe ends early, which `_iter_with_data` maps to `TruncatedError`
(`rar_reader.py:488-491`). So solid truncation surfaces *an* honest error; the
nonsolid single-member path does not.

**Why it matters (VISION #3).** "Damaged input is a first-class citizen — truncation
becomes recoverable members plus an honest error." A member `unrar` fails to emit
should raise a typed error (`CorruptionError` / `TruncatedError`), not return a short
buffer. Map the known `unrar` codes (2/3/10 → corruption/truncation), and/or have the
nonsolid path assert it received `size` bytes (as the solid path effectively does).

---

## Small note — `rc==11` raise inside `finally` masks an inner-close error

`_UnrarOwnedStream.close` (`rar_reader.py:145-164`) does `self._inner.close()` in the
`try`, and the reap + `rc==11` raise in the `finally`. If `self._inner.close()`
raises, the `EncryptionError` from the `finally` replaces it (Python `finally`
semantics). Low impact — the inner is a pipe wrapper whose close rarely raises — but
if it ever does, the original error is lost. Reaping in the `finally` is right;
consider raising the password error only when `self._inner.close()` did not itself
raise.

## What's fine at this boundary

- **`shell=True` is not used**; the command is an argv list, so classic shell
  injection is impossible — F3 is switch/list-file injection into `unrar` itself, not
  a shell escape.
- **Password is passed as `-pPWD`**, visible on a process listing, but that is
  inherent to the `unrar` CLI and unavoidable without a temp password file; the code
  correctly uses `-p-` to *disable* the interactive prompt when no password is set,
  so `unrar` never blocks waiting on stdin.
- **Every process is reaped.** `_UnrarOwnedStream.close` waits/terminates,
  `terminate_unrar` polls-then-SIGTERM-then-SIGKILL with timeouts, the stream is
  `_track_decompressed`-registered so reader teardown closes it, and `_close_archive`
  terminates `_live_unrar` as a backstop. No zombie/hang path found, including on
  `BaseException` mid-stream.
- **`find_rarlab_unrar`** correctly rejects `unrar-free`/`unar` by banner sniffing and
  raises `PackageNotInstalledError` (not a raw `OSError`) when the binary is missing,
  including when `Popen` itself raises `OSError` (`rar_unrar.py:92-93`).
