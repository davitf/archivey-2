## MODIFIED Requirements

### Requirement: Coverage-guided fuzz gate for parsers and entry points

The test suite SHALL provide an Atheris (libFuzzer) coverage-guided fuzz harness
over archivey-owned hostile-input entry points. The harness MUST seed from the
declarative corpus and adversarial fixtures, force accelerators off, and treat
success as: within each time budget, only typed `ArchiveyError` subclasses or
clean returns — never an uncaught non-`ArchiveyError` exception, process abort,
or hang past the slice timeout.

For CRC/checksum-gated targets (native 7z header parse at minimum; ZIP member
headers/payloads when the ZIP read path is exercised; RAR headers; other formats
when their interesting paths sit behind a header CRC), the harness SHALL apply a
**mutate-then-fixup** step that recomputes and patches valid CRC fields before
invoking the parser/reader, so coverage guidance reaches post-CRC logic. It MUST
NOT rely on unaided libFuzzer CMP feedback to solve CRC32. A minority of inputs
(or a small dedicated budget) SHALL retain broken CRCs so the reject path stays
exercised.

The default main-branch run SHALL partition a wall-clock budget of approximately
150–170 seconds across these targets (exact seconds MAY be env-overridable):

| Target | Role |
| --- | --- |
| Native 7z header parse | Deep coverage of the pure-Python header parser |
| 7z `open_archive` + member list/materialize | Reader/spine path after parse |
| `detect_format` over arbitrary/prefix seeds | Magic/peek-replay entry point |
| ZIP `open_archive` + member list + bounded member `open`/`read` | Wrapper + native codec/AES read path; mutate local/CD headers and compressed content with CRC fixup |
| TAR `open_archive` + member list | Shallow wrapper/translation coverage |
| ISO `open_archive` + member list | Shallow; MUST use a hard wall-clock kill timeout |
| Native RAR header parse | Deep coverage of the pure-Python RAR3/RAR5 metadata parser (CRC mutate-then-fixup) |
| RAR `open_archive` + member list | Reader/spine path after parse |
| Stream/codec (unix-compress at minimum) | Direct codec-stream hostile input; MUST use a per-input wall-clock kill timeout when hang classes are known |

The Atheris CI workflow SHALL install RARLAB `unrar` on Linux so the RAR
open+list target is not skipped solely for missing decompressor binary. RAR
targets MAY still skip cleanly when the RAR backend is not registered.

Full member **extract** remains out of scope for this harness (covered by the
mutation harness). Additional stream/codec targets MAY be added without removing
the above.

CI SHALL run the harness on every push to `main` and via `workflow_dispatch`
(longer budgets allowed). It MUST NOT be part of the default pull-request test
matrix. On failure the job SHALL upload reproducing inputs as artifacts and
print a one-line local re-run command. Always-on nightly schedules are not
required.

The existing corpus mutation harness and Hypothesis property tests remain
mandatory complementary layers; Atheris does not replace them. `atheris` is
installed only via the CI `fuzz` dependency group (`packaging-and-extras`).

#### Scenario: atheris gate matrix

| Case | Expected |
| --- | --- |
| Push to `main` | Fuzz workflow runs partitioned ~150–170s budget; green if no crash/hang/raw exception |
| `workflow_dispatch` with longer env budget | Same targets; extended exploration |
| Pull request (default matrix) | Atheris job not required |
| RAR backend absent | RAR targets skipped; other targets still run |
| RAR backend present + `unrar` installed (Atheris CI) | RAR open+list target runs (not skipped for missing binary) |
| Fuzzer finds a crashing input | Job fails; repro bytes uploaded; re-run command printed |
| 7z / RAR header target with fixup enabled | Most iterations present a matching header CRC and enter post-CRC parse |
| ZIP target with fixup + bounded read | Post-CRC / post-local-header path reaches archivey codec or AES+codec stream; typed errors only |
| Stream/codec target (unix-compress) | Hostile `.Z` inputs exercise decode/seek-index without raw exceptions; hang → slice failure with artifact |
| Broken-CRC sample / minority path | Typed CRC/corruption failure; reject path still hit |
| Mutation harness / `ARCHIVEY_FUZZ` | Still available and unchanged in role |
