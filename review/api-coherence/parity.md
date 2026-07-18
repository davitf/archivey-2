# Cross-backend parity audit

The load-bearing claim: every observable on `ArchiveMember` / `ArchiveReader` means the
same thing on every backend. Verdict: **true for almost everything** — the team has
clearly worked this seam (hashes matrix, timestamp faithfulness, cost-receipt fixes) —
with **one substantive break (P1)** and a couple of small divergences.

## P1 (High) — duplicate-name members: three formats, three behaviors

`is_current` is defined as "live final state of its path (last-entry-wins)"
(`types.py:317`, `archive-data-model` spec). Who actually computes it:

| Backend | Same-name duplicates | Result |
|---|---|---|
| 7z | `compute_is_current` (`sevenzip_parser.py:331`), incl. anti supersession | earlier entry `is_current=False` → default extract `SKIPPED`, last wins |
| RAR | history rows get distinct names `path;n` + `is_current=False` (`rar_reader.py:505`) | default extract writes live row only |
| ZIP | **not computed** — all entries `is_current=True` | second write is an O2 collision → default `OverwritePolicy.ERROR` → **`ExtractionError`, extraction halts** |
| TAR | **not computed** | same as ZIP |
| ISO / directory | duplicates impossible (filesystem tree) | n/a |

Runnable repro (committed as scratch during review; trivially recreated):

```python
import io, tarfile, zipfile
from archivey import open_archive

with zipfile.ZipFile("d.zip", "w") as z:
    z.writestr("a.txt", "old"); z.writestr("a.txt", "new")
with tarfile.open("d.tar", "w") as t:
    for c in (b"old", b"new"):
        ti = tarfile.TarInfo("a.txt"); ti.size = len(c)
        t.addfile(ti, io.BytesIO(c))

with open_archive("d.tar") as r:
    print([m.is_current for m in r.members()])   # [True, True]
    r.extract_all("out")   # ExtractionError: Destination already exists: out/a.txt
```

The same two-entry archive as 7z extracts fine (old entry `SKIPPED`). Observed output
for zip and tar:

```
'a.txt' is_current=True size=3
'a.txt' is_current=True size=3
extract FAILED (default policy): ExtractionError: Destination already exists: .../a.txt
```

Why this matters beyond symmetry:

- **An appended-to tarball (`tar -rf archive.tar file`) is a normal artifact**, and GNU
  tar / bsdtar / unzip all extract it last-entry-wins. Archivey's default refuses it.
  "Read every format behind one uniform interface" is undercut in both directions:
  per-format divergence, and divergence from every reference tool.
- **The specs already disagree with the implementation.** `safe-extraction`'s
  non-current-skip scenario says "Content superseded by later same-name or anti →
  `SKIPPED` on extract" with *no format qualifier* (`safe-extraction/spec.md:230`),
  while `archive-data-model:206` scopes the same statement "(7z)". ZIP/TAR satisfy
  neither reading cleanly. Per the pause-and-ask rule this is **Q1 in QUESTIONS.md**,
  not something this review resolves.
- **The conformance sweep works around it instead of asserting a contract**:
  `test_corpus_sweep.py:203` passes `overwrite=REPLACE if has_duplicates else ERROR` —
  the harness itself needed the escape hatch. Whatever Q1 decides, the sweep should
  assert the *uniform* duplicate contract (same corpus entry, every format, same
  default outcome) rather than dodging it.
- Small doc casualty: `ArchiveReader.get()`'s docstring sells "the last (the one a
  sequential extraction would leave on disk)" (`reader.py:114`) — for ZIP/TAR under
  default policy, sequential extraction leaves an error, not a file.

Recommended direction (argued in `members-scope.md`): compute last-entry-wins
`is_current` in every random-access materialization (ZIP central directory and TAR
scan both see all entries before publishing the list), keep exact-same-name
supersession out of the O2 collision map (O2 stays for *distinct* stored names that
collide post-normalization — a genuinely different hazard), and document the one
honest streaming-mode caveat (a forward-only TAR pass cannot know `is_current`
mid-pass; in `streaming=True` extraction, same-name entries follow overwrite policy
— which is what bsdtar does). Migration cost: pre-release, **free** at the API level;
one spec delta + backend materialization change + sweep assertion.

## P2 (Low-Med) — RAR listing cost: implementation says INDEXED, docstring says otherwise

`ListingCost.REQUIRES_SCANNING`'s docstring names its canonical examples: "an
uncompressed tar, **or a RAR with no quick-open record**" (`cost.py:24-27`). The RAR
backend reports `INDEXED` unconditionally (`rar_reader.py:773`). The native parser does
walk headers file-by-file at open (there is no central index in RAR without the
quick-open record), so by the receipt's own definition ("describes static open-time
properties" — `access-mode-and-cost` spec) the honest value for a no-quick-open RAR is
arguably `REQUIRES_SCANNING`; by the "cost you'll pay *now*" reading it's `INDEXED`
because open already paid the scan. The two public artifacts disagree — **Q3**.
(`access-mode-and-cost`'s example matrix lists ZIP/tar/7z but is silent on RAR, so the
spec doesn't break the tie.)

## The rest of the matrix — checked, uniform

| Observable | Verdict | Evidence |
|---|---|---|
| `hashes` | **Uniform & documented.** ZIP crc32 (FILE/SYMLINK), 7z crc32 (FILE, when stored), RAR5 crc32+blake2sp, `.gz` trailer crc32 (single-member, seekable), `.lz` under declared SEEKABLE, none for tar/dir/ISO/bz2/xz — exactly the matrix in `docs/formats.md` §Stored digests. Emptiness contract is explicit ("when the backend documents them"). | `zip_reader.py:627`, `sevenzip_reader.py:349`, `rar_reader.py:138-140`, `codecs.py:768,912` |
| `CostReceipt` axes | **Honest and orthogonal** (except P2). Directory = `REQUIRES_SCANNING` (old #4/#9 fixed, with the reasoning in a comment); compressed tar = `REQUIRES_DECOMPRESSION`+`SOLID`+`solid_block_count=1`; plain tar = `REQUIRES_SCANNING`+`DIRECT`; 7z = `INDEXED` + `SOLID`/`DIRECT` + real folder count; single-file reports the *actual* source capability rather than assuming seekable. Per-format test: `test_cost_receipt.py::test_cost_receipt_per_format`. | backends' `_get_archive_info` |
| `stream_capability` | Uniform meaning (source seekability). TAR/single-file compute from the real source; ZIP/7z/RAR/ISO hardcode `SEEKABLE`, defensible because their open fails fast on non-seekable sources (`SUPPORTS_STREAMING_NON_SEEKABLE=False`). | grep of backends |
| `MemberStreams` gating | Uniform: every backend (including directory, deliberately) honors default forward-only/one-live-stream and unlocks via declared flags; undeclared SEEKABLE forces `seekable() == False` even when the inner handle could seek (`archive_stream.py:328`). | `directory_reader.py:4-6`, backend greps |
| timestamps | Faithful per format (naive DOS/RAR4 wall-clock vs aware UTC/offset elsewhere), hostile values degrade to `None` + `MEMBER_TIMESTAMP_INVALID` via the shared helper; `modified_utc()` is the explicit conversion. This is divergence *by design and documented on the field*, which is the right kind. | `timestamps.py`, `types.py:372-392` |
| `mode` | Same meaning everywhere it's set (Unix permission bits): tar `S_IMODE`, dir `S_IMODE(st_mode)`, ZIP from Unix create_system, ISO Rock Ridge, RAR/7z from Unix-host attrs; `None` where the format doesn't carry it. Sweep asserts per-format via `_MODE_FORMATS`. | backends; `test_corpus_sweep.py:45-57` |
| `MemberType` incl. `ANTI` | `ANTI` is 7z-only by nature; classification, `stream=None`, and usage-error open/read are spec'd and tested (`test_sevenzip_reader.py:703+`). Non-file open/read raises `ArchiveyUsageError` uniformly. | `format-7z` spec, `error-handling` spec |
| link model | `link_target` (raw stored string) + `link_target_member` (resolved) uniform; resolution rules (latest earlier same-name for tar hardlinks, etc.) centralized in `base_reader.py`, not per-backend. | `base_reader.py:732-830` |
| `member_count` | Honest: len for ZIP/RAR/7z/single-file, `None` (with a comment saying why) for tar/dir/ISO. | grep above |

## Where the sweep should assert parity but doesn't

1. **Duplicates** — the `REPLACE if has_duplicates` dodge (`test_corpus_sweep.py:198-205`).
   After Q1: one corpus entry, every format that can express duplicates (zip, tar,
   7z — extend `sample_archives` builders), assert identical `is_current` pattern and
   identical default-extract outcome.
2. **`is_current` is asserted only in per-format tests** (`test_sevenzip_reader`,
   `test_rar_reader`) — there is no cross-format statement that *every other* backend
   yields all-`is_current=True` for unique-name corpora. One sweep-level assertion
   (`all(m.is_current for m in members)` for entries without duplicates) makes the
   field's meaning a tested invariant instead of a per-backend accident.
3. **Cost receipts** are tested per-format in `test_cost_receipt.py` (good) but the
   *docstring examples* in `cost.py` are not tied to it — P2 survived because nothing
   cross-checks prose against receipts. Cheap fix: when Q3 is decided, encode the
   RAR row in `test_cost_receipt_per_format` and fix whichever artifact lost.
