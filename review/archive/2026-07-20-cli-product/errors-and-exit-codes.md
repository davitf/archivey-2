# Errors, exit codes, and the honest-damage story

Lens C of the brief: VISION #3 ("damaged input is a first-class citizen —
recoverable members + an honest error") as experienced at the shell, plus the
error-prose audit. Real transcripts (config noted where it matters).

## Exit codes: the contract holds

Everything probed matched the spec'd 0/1/2 shape, uniformly:

| Invocation | Exit | Notes |
|---|---|---|
| `l photos.zip` / `x` / `t` / `info` success | 0 | |
| `l many.zip \| head -3` | 0 | BrokenPipe clean, no stderr noise |
| `l nope.zip` (missing) | 1 | |
| `l random.bin` (not an archive) | 1 | |
| `t badcrc.zip` (digest mismatch) | 1 | `3 OK, 1 failed` |
| `x badcrc.zip` / `x evil.tar` (stop) | 1 | |
| `t --password wrong secret.7z` | 1 | |
| `--badflag`, unknown flag, missing archive | 2 | argparse |
| `create` / `hash` / `convert` / `cat` / `--salvage` / `-` | 2 | unified "not yet" (#131 D7 done) |
| Ctrl-C | 130 | code path + tests; not exercisable here (extracts too fast) |

`test`'s pass/fail contract is crisp: quiet `N OK, M failed` on stderr, 1 on
any failure, 0 otherwise — scripts can branch today. The "codes ≥3 reserved,
don't assume 1 is exhaustive" documentation stance is right and leaves room
for the P1/P2 recommendations below without breaking anyone.

## P1 — the honest-damage story fails at the shell (0.2.0, needs Q1)

The founding claim is "recoverable members + an honest error." `test`
delivers it (#131 D3 fixed: failures are counted and the run continues). But
`extract` — the verb in the demo, the verb in the README — delivers neither:

**Damaged archive** (one flipped byte in `docs/guide.md`, 4-member zip):

```
$ archivey x badcrc.zip -v
extracting into badcrc/
Digest mismatch for 'crc32': stored value does not match the decompressed
  content. (archive='badcrc.zip', member='docs/guide.md', format=ArchiveFormat.ZIP)
extraction stopped; remaining members were not extracted
                                                 exit 1
$ find badcrc/
badcrc/readme.txt        ← extracted, but nobody said so
badcrc/docs/             ← empty
```

`readme.txt` and `setup.py`/`main.py` are perfectly recoverable; the CLI
stopped at member 2 of 4 and — even with `-v` — never reported which members
*were* written (the stop path bypasses `_report_extraction`,
`cli/extract_cmd.py:392-400`). "Remaining members were not extracted" is
honest about the future but silent about the past.

**Hostile archive** (one `../escape.txt` member + three safe entries):

```
$ archivey x evil.tar -d evilout
Path traversal ('..') in member name: '../escape.txt' (member='../escape.txt')
extraction stopped; remaining members were not extracted
                                                 exit 1   (0 files on disk)
```

Identical under `--policy standard` and `--policy trusted`. So the flagship
"safer unzip" comparison currently reads: `unzip` warns on the `../` entry,
**extracts the three safe files**, exits 1 — archivey extracts **nothing**.
The safety block is right; refusing to deliver the deliverable members is the
part that loses the demo. And the machinery to do better already exists and
is *dead code in practice*: `_report_extraction` prints `rejected: <name>:
<why>` lines and a `N rejected` summary column (`cli/extract_cmd.py:315-336`)
that can only trigger if the library records rejections instead of raising —
which `OnError.STOP` (the library default the CLI inherits) never does.

**Recommendation:** make the CLI extract with reject-and-continue semantics —
policy rejections and per-member read failures are recorded (`rejected:` /
`failed:` lines), extraction continues where the stream allows, summary shows
`3 extracted, 1 rejected → evilout/`, exit nonzero. Whether that's the CLI
passing an `OnError`-style option, a new library default, or CLI-only is a
maintainer call (Q1) — as is whether "stopped early" should also list what
was already written (cheap: the report is in hand at the except site for the
`ExtractionReport`-carrying failure paths; for raised paths, at least count).
Note this is *not* `--salvage` (decoding damaged data best-effort); it is
completing the members that are fine. The reserved exit code 3 ("refused by
safety policy", design Decision 12) becomes genuinely useful here.

## Error prose audit (P5, P6, P7)

The brief asks: does each common failure produce a message a human can act
on, or a translated-exception repr? Verbatim results, worst first:

| Scenario | Message (verbatim) | Verdict |
|---|---|---|
| Ctrl-D at password prompt | 30-line traceback ending `EOFError` | **P5, bug.** Catch `EOFError` in `cli/password.py:19` provider → return None ("no password given" flow). Reproduced under a real PTY; exit 1 with raw traceback. |
| Verb typo: `archivey lsit photos.zip` | `[Errno 2] No such file or directory: 'lsit'` | **P6.** Raw errno repr; the archive arg silently became a filter. Minimum: `archivey: cannot open 'lsit': no such file or directory`. Better: when open fails ENOENT and pattern positionals exist, add `('lsit' was treated as an archive path; verbs are list/test/extract/info)`. |
| Missing file: `l nope.zip` | `[Errno 2] No such file or directory: 'nope.zip'` | **P6.** Same errno repr for the most common error a CLI ever prints (`unzip: cannot find or open nope.zip`). One `except OSError` formatting site (`cli/main.py:362-364`) fixes both rows. |
| Truncated zip | `Could not open ZIP archive: BadZipFile('File is not a zip file') (archive='truncated.zip', format=ArchiveFormat.ZIP)` | **P7.** Nested exception repr *and* misleading content ("not a zip file" for a file that is a truncated zip — magic matched!). Library-side message; the CLI is where it's felt. |
| Not an archive | `Could not detect archive format: no magic-byte match and no usable file extension. (archive='random.bin')` | Good prose; the parenthetical context suffix is tolerable. |
| Wrong password (7z) | `FAIL: Wrong password or corrupt 7z folder` | Good — honest about the ambiguity. |
| No password, non-TTY (7z) | `FAIL: Password required to read this encrypted member` | Good. |
| No password, non-TTY (zipcrypto) | `FAIL zc.txt: Wrong password for this ZIP member` | **P7 / #131-D8 residue:** says "wrong password" when *none was supplied* — reads as "my flag was ignored". |
| Encrypted RAR headers, no password | `Password required to decrypt RAR headers` | Good. |
| AES 7z, core-only install | `The 'cryptography' package is required for AES decryption (install the 'crypto' extra).` | **Excellent** — names the package, the extra, the operation. |
| RAR data, no `unrar` binary | `RARLAB unrar is required to read RAR member data, but it was not found on PATH (or the unrar on PATH is not RARLAB unrar). Install RARLAB unrar — unrar-free / unar / 7z are not supported as substitutes.` | **Excellent**, best message in the tool. Listing still works without it — the degradation story is exactly right. |
| tar.zst, core-only install | `Format ArchiveFormat.TAR_ZST is not available: missing backports.zstd (pip install archivey[zstd]) (format=ArchiveFormat.TAR_ZST)` | Actionable, but leaks the enum name twice (P7); `tar.zst` is the human spelling. |

Pattern in P7: the `ArchiveyError` tree's messages are mostly *good prose
already* — the leaks are (a) wrapped third-party exception reprs
(`BadZipFile(…)`), and (b) `ArchiveFormat.X` enum names in interpolations.
Both are library-side string hygiene, worth one targeted pass since the CLI
surfaces them verbatim (correctly — the CLI should not be rewriting library
messages).

## P8 — `test` counts vs archive-wide failures

```
$ archivey t hardlinks_solid__.rar        # 4 members, unrar absent
FAIL: RARLAB unrar is required to read RAR member data, …
0 OK, 1 failed                                   exit 1
```

Exit code and message are right; `0 OK, 1 failed` is not — one FAIL line
represents a whole-archive condition and three members were never tested
(solid-stream aborts have the same shape: the generator dies, remaining
members are silently lost — the known library limitation noted at
`cli/test_cmd.py:56-60`). When the iterator ends early relative to the
selected set, append the honest remainder: `0 OK, 1 failed, 3 not tested`.
The selected count is already computed when an index exists (it feeds the
progress total).

## P9 — tripped library bug: misleading accelerator warning on `[all]`

```
$ archivey x src.tar.gz          # rapidgzip 0.16.0 IS installed
WARNING: Seeking backward in a gzip stream without a random-access
accelerator re-decompresses from the start (O(n) per rewind). Install the
'seekable' extra (rapidgzip) for indexed random access.
```

Reproduced via plain library calls too (`open_archive(...).extract_all(...)`)
— not a CLI defect, but the CLI's new logging handler (#131 D4) puts it in
every tar.gz demo. Cause: `use_rapidgzip=AUTO` declines the accelerator below
the size threshold (`internal/streams/codecs.py:239-251`), then the
backward-seek warning (`internal/streams/archive_stream.py:408`) assumes
"no accelerator" means "not installed" and tells the user to install what
they have. For a sub-MB file the rewind costs microseconds — the warning
shouldn't fire at all there, and its text should distinguish "not installed"
from "not engaged (below size threshold)". Filed as CLI-visible because the
first `x something.tar.gz` a new user runs prints a scary WARNING with wrong
advice on a default `[recommended]` install.

## What is actually fine (this lens)

- The stop notice itself (`extraction stopped; remaining members were not
  extracted`) is honest and well-worded — P1 is about what it doesn't say
  and what didn't happen before it.
- `--overwrite error` stops with `Destination already exists: out/readme.txt
  (member='readme.txt')` — clear, correct exit 1.
- Hostile *paths* are blocked under every policy (traversal above; the
  name-safety layer extracted my ANSI-escape-named files as-is on Linux,
  which is that capability's documented Linux posture, not a CLI issue —
  the CLI-side display problem is P3 in `output.md`).
- `info` on failure: prints the identity block it could determine, then the
  open error, exit 1 — the "answer what you can" shape is right.
- Exit 2 flavor unified across the whole reserved surface; scripts can rely
  on "2 = you asked for grammar that doesn't exist (yet)".
