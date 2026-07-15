# `unrar` subprocess boundary (`rar_unrar.py`, `rar_reader.py`)

RARLAB `unrar` 7.00 was installed (per `AGENTS.md`: `apt-get install -y unrar`,
`multiverse` component) and both findings below are **empirically confirmed** — at the
`unrar` CLI level (`repro.py` F3b) and, for F3, **end-to-end through archivey** against
committed adversarial fixtures. `make_hostile_fixtures.py` (in this folder) built
`tests/fixtures/rar/hostile_argv__.rar` (RAR5) and `hostile_argv__rar4.rar` (RAR4) — the
`rar` writer isn't available in the review container, so the maintainer ran it locally
and pushed the results. Each archive is nonsolid + compressed with three members:
`canary.txt` (control) and two hostile names, `-inul` (a `unrar` switch) and `@atfile`
(a `@listfile` arg), verified to be stored verbatim and compressed (so archivey routes
them through `unrar`, not a direct read).

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

Concrete consequences from a crafted archive, **confirmed against RARLAB unrar 7.00**
(`repro.py` F3b, run on `basic_nonsolid__.rar` which has members `file1.txt`, … and
whole-pipe output `Hello, world!Hello, universe!Hello there!`):

```
real member 'file1.txt'   -> exit 0, b'Hello, world!'
member '-inul' (a switch) -> exit 0, b'Hello, world!Hello, universe!Hello there!'  # ALL members
member '@list.txt'        -> exit 0, b'Hello, world!'                              # read a LOCAL file
'-- -inul' (`--` guard)   -> exit 10, b''                                          # switch neutralized
'-- @list' (`--` guard)   -> exit 0, b'Hello, world!'                              # @ STILL expands
```

- **`-`-prefixed name → switch injection, and it returns the WRONG bytes.** A member
  named `-inul` (or any `unrar` switch) is consumed as a switch, which leaves `unrar`
  with *no member filter* — so it prints **every member's data concatenated**, exit 0.
  The nonsolid `_open_member` path then slices the first `size` bytes off that
  concatenation and hands them back as "this member". So `open("-inul")` silently
  returns bytes belonging to *other* members — a content-confusion bug, not merely an
  empty read. (A stored CRC on the named member would catch the mismatch via
  `VerifyingStream`; a CRC-less member would not.)
- **`@`-prefixed name → arbitrary local-file read.** A member named `@somepath` makes
  `unrar` read `somepath` from disk (relative to CWD) as a newline-separated list of
  names to extract — confirmed above (it opened `list.txt` and honoured its contents).
  The untrusted archive thus steers `unrar` into opening an attacker-chosen local
  path. Under `p -inul` nothing is written to disk, but it is an unintended local-file
  access driven purely by a member name.

**End-to-end through archivey** (committed fixtures, from a scratch CWD):

```
open_archive("hostile_argv__.rar")     # RAR5;  RAR4 variant identical
  read "canary.txt" -> MATCH (1408 B)
  read "-inul"      -> CorruptionError: Digest mismatch for 'crc32'
  read "@atfile"    -> CorruptionError: Digest mismatch for 'crc32'
```

So opening either hostile member does **not** return its bytes: `unrar` emits the
wrong data (all members / a local file), the size-`n` slice fails CRC verification,
and archivey raises `CorruptionError`. The canary reads fine, confirming the archive
itself is sound and the failure is specifically the mis-parsed member name.

**The `@atfile` local-file read, shown directly.** Planting a listfile named `atfile`
in the CWD and running archivey's own command (`unrar p -inul <archive> @atfile`) makes
`unrar` read that local file and emit whatever members it names — here the canary's
1408 bytes. A hostile *member name* thus drives `unrar` to open an attacker-chosen
local path. (Because the name doesn't match a real member, the bytes still fail the
`@atfile` member's CRC inside archivey — but the local-file access already happened.)

This is precisely the axis the spec's **"Constrain unrar argv by call site"**
requirement exists to protect (`openspec/specs/format-rar/spec.md`: *"MUST NOT pass
… globs, or `@listfile` filters"*). The current code honours that for the argv *the
backend intends to build*, but not for the case where the hostile **member name
itself** is a switch/`@listfile`. The spec's own constraint is therefore only
half-enforced.

Note the CRC nuance: because normal `rar` output always carries a CRC32, the
wrong-bytes confusion is *caught* here and surfaces as `CorruptionError` (itself a
mislabel — you asked for a member and got a corruption error). The silent wrong-bytes
outcome needs a CRC-less member; the `@`-listfile local-file access, however, happens
regardless of CRC.

**Suggested fix (updated after testing — see `QUESTIONS.md` Q2).** The simplest
uniform fix is to stop passing the member as a positional argument and instead use the
**`-n` include-mask switch with a `./` prefix**: `unrar p -inul … -n./<member>
<archive>`. Because the hostile character is now *inside* the `-n` switch value (which
itself starts with `.`), neither a leading `-` (not a switch) nor a leading `@` (not a
listfile) is special. Confirmed against RARLAB unrar 7.00: `-n./-inul`, `-n./@atfile`,
`-n./canary.txt`, and `-n./subdir/file2.txt` each extract exactly their member.

`--` alone is **not** sufficient — it neutralizes the `-` switch case (`-- <archive>
-inul` → exit 10) but does **not** stop `@`-listfile expansion (`-- @list` still read
the file), which is why `-n./` (or rejecting `@` names) is required. Two caveats for
`-n./`: (1) `-ver` history rows need `-ver` added to the call (the mask excludes them
otherwise — reuse the existing `version_control` plumbing); (2) `-n` values are masks,
so literal wildcard metacharacters (`* ? [ ]`) in a name match as wildcards — safe here
because CRC + the Q3 length check catch any mis-match, but optionally reject such names
for a precise error. Member *listing* keeps the original names regardless.

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
`-- <switch-name>` case) — the reader hands back a **silently truncated or empty**
stream and swallows the non-11 exit code.

**Confirmed exit codes (RARLAB unrar 7.00):** a corrupted archive exits **3**
(`repro.py` corrupt-member check: `unrar p` on a byte-flipped fixture → exit 3), and
a non-matching name exits **10** (`repro.py` F3b `-- -inul` → exit 10, empty output).
Neither is mapped by archivey — only 11 is.

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
