# Output — human and machine

Lens B of the brief: the default views, machine-readability, `info`, progress,
and `--track-io`. Real transcripts throughout (config `[all]` unless noted).

## The default `list` view: good bones

```
$ archivey l links.tar
f-  rw-r--r--           6  2026-07-18 04:26  data/readme.txt
l-  rw-r--r--           -  1970-01-01 00:00  data/link-to-readme -> readme.txt

$ archivey l secret.7z
fE  rw-------          11  2026-07-18 04:26  secret.txt

$ archivey l --digests photos.zip
f-  rw-r--r--           6  2026-07-18 04:26  readme.txt  [crc32=363a3020]
```

- Scannable and aligned: type+encrypted as a compact two-char lead, mode,
  right-aligned size (10 wide — fits TB-scale), minute-precision mtime, name
  last (greppable). Link targets shown `->` style. Hardlinks marked `h`.
  Missing values are `-`. This is a good layer-1.
- **Scale:** 10 000 members list in ~0.4 s wall (`l many.zip > /dev/null`),
  constant-width columns hold. No pager, no truncation — correct for a
  compose-with-`less` Unix tool.
- **Piped:** clean. `archivey l many.zip | head -3` → three rows, exit 0, no
  stderr noise (BrokenPipe handled). Data on stdout only; `--track-io`,
  summaries, prompts, progress on stderr — verified across verbs.
- Quiet `test` default + `-v` per-member trace matches `unzip -t`/`gzip -t`
  norms and reads well:

```
$ archivey t -v badcrc.zip
OK   readme.txt
FAIL docs/guide.md: Digest mismatch for 'crc32': stored value does not match
  the decompressed content. (archive='badcrc.zip', member='docs/guide.md', format=ArchiveFormat.ZIP)
OK   setup.py
OK   main.py
3 OK, 1 failed                                   exit 1
```

## P3 — member names are a terminal-injection vector (0.2.0)

`format_member_line` prints `member.name` raw (`cli/format.py:64-68`), and so
do `test`'s FAIL lines, `extract`'s `renamed:`/`rejected:` notices, and the
tqdm bar description. Archive names are attacker-controlled. Demo (`cat -v`
view of actual bytes reaching the terminal):

```
$ archivey l hostile-name.zip
f-  rw-------           1  2026-07-18 04:26  innocent.txt
f-  rw-------           1  2026-07-18 04:26  evil^[[31mRED^[[0m.txt
f-  rw-------           1  2026-07-18 04:26  line1^MOK  everything fine.txt
```

On a real TTY the second row renders partially red and the third **rewrites
its own line** to `OK  everything fine.txt` — name-spoofing inside the tool
whose pitch is "inspect untrusted archives safely." Escapes can also probe
terminal features (OSC sequences). GNU `ls` and `tar` both quote control
bytes for exactly this reason; `unzip` strips high-bit/control characters.

**Recommendation:** escape C0/C1 control bytes (and DEL) in every member name
the CLI prints — backslash-escape (`\r`, `\x1b`) or U+FFFD replacement — at
minimum when the destination stream is a TTY; quoting unconditionally is
simpler and safer for logs. One helper in `cli/format.py` used by list/test/
extract notices and the tqdm `desc` covers the surface. (The *extracted
filenames* are the name-safety capability's job; this is only about output.)

## P4 — the machine-readable gap (the named scripting audience)

There is no `--json`, `--porcelain`, `-0`, or any stability promise on the
human columns. VISION explicitly names the "iterate members and hash"
scripting audience; today their options are (a) parse aligned columns whose
format the docs never specify, or (b) import the library — and (b) is the
answer the *library* wedge wants, but the CLI wedge loses the
shell-scripting half (dedupe pipelines, CI manifest checks, `jq` users).
The `cli-v1` design consciously deferred `--json` pending a stable member
schema (design.md Open Question 7, "highest-value deferred item").

This review's job is to name the gap's cost: `list --digests` output
(`[crc32=363a3020]`) is precisely the "members and hash" payload, trapped in
a display format. A minimal contract — `--json` on `list`/`info` emitting one
object per line (name, type, size, mtime, mode, encrypted, link_target,
hashes) with a documented "fields may be added, not renamed" promise — is
cheap (stdlib json, the CLI already has every value in hand) and doesn't
block on the full `ArchiveMember` schema question. Timing decision at Q2.

## `info`: answers "what is it", not yet "can I read it / what will it cost"

```
$ archivey info photos.zip              $ archivey info -v secret.7z
path:        photos.zip                 path:        secret.7z
format:      ZIP                        format:      SEVEN_Z
confidence:  certain                    confidence:  certain
detected_by: magic                      detected_by: magic
version:     -                          version:     0.4
solid:       False                      solid:       False
encrypted:   False                      encrypted:   True
multivolume: False                      multivolume: False
members:     4                          members:     1
                                        extra.7z.volume_count: 1
```

Good: aligned, human, member-free, degrades correctly (detection failure →
one-line error, exit 1; open failure after successful detection still prints
the identity block, then `open: <error>`). Two gaps against the brief's
"format, confidence, encryption, **the CostReceipt story**" bar (P14):

- **Format label leaks the enum name**: `SEVEN_Z` (and `TAR_GZ` etc. — the
  label is `repr(fmt)` minus the class prefix, `cli/info_cmd.py:16-23`).
  `7z` / `tar.gz` are the human names; the enum has `file_extension()`
  available. Cosmetic but it's the *first* line a "what is this file?" user
  reads.
- **No cost/access story.** The library sells honest cost signals
  (`reader.cost`, solidity implications, seekability), and `info` is the
  natural place to say "solid 7z: reading one member decodes the folder
  prefix" or "no random-access accelerator for .gz installed". Today
  `solid: False` is as far as it goes. Even one derived line
  (`access: random (indexed)` / `access: sequential-only`) would make `info`
  answer its third question. Shape at Q5.
- Related (Q6): neither `info` nor `--version` says *what this install can
  read* — no dependency/extras matrix (design Decision 10 originally listed
  one for `--version`). The excellent per-format "install the X extra"
  errors partially compensate, but only after you've tried and failed.

## Progress: right behavior, verified both ways

Under a PTY (80 MB zip, `x big.zip -d bigout`):

```
data/blob00.bin:   1%|▌         | 1.00M/80.0M [00:00<00:00, 169GB/s]
...
data/blob39.bin: 100%|██████████| 80.0M/80.0M [00:00<00:00, 428MB/s]
40 extracted, 0 renamed, 0 skipped → bigout/
```

Member name as description, byte totals, instant appearance on fast extracts
(the `mininterval=0` choice works), a final 100% line, closed before the
summary prints. `test` gets the same bar. Piped/non-TTY: no bar, no noise.
Core-only venv (no tqdm): silent degradation, command fully functional.
`--hide-progress` honored. **Endorsed** — with the P3 caveat that the bar's
`desc` needs the same name-escaping as everything else.

## `--track-io`: fine for what it claims to be

```
$ archivey t --track-io photos.zip
track-io: bytes_decompressed=1518 compressed_bytes_consumed=- source_seek_count=16
$ archivey info --track-io photos.zip
track-io: n/a for info (no member-body decode)
```

Raw counters on one stderr line, `-` for unavailable, explicit n/a on `info`.
As a maintainer/debug affordance (its documented role) this is fine; resist
the temptation to humanize it. No change requested.

## P8 — `test` counts under archive-wide failure (also in errors file)

`t hardlinks_solid__.rar` without `unrar` (4 members) → `FAIL: RARLAB unrar
is required…` + `0 OK, 1 failed`. One "member" failed, three were never
tested, and the summary can't tell those apart. Suggested shape:
`0 OK, 1 failed, 3 not tested` whenever the iterator dies before yielding
all selected members (the selected-count is already computed for progress
totals when an index exists). Message content itself is excellent.
