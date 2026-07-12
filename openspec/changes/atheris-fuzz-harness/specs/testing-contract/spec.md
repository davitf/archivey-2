## ADDED Requirements

### Requirement: Coverage-guided fuzz gate for parsers and entry points

The test suite SHALL provide an Atheris (libFuzzer) coverage-guided fuzz harness
over archivey-owned hostile-input entry points. The harness MUST seed from the
declarative corpus and adversarial fixtures, force accelerators off, and treat
success as: within each time budget, only typed `ArchiveyError` subclasses or
clean returns — never an uncaught non-`ArchiveyError` exception, process abort,
or hang past the slice timeout.

The default main-branch run SHALL partition a wall-clock budget of approximately
120 seconds across these targets (exact seconds MAY be env-overridable):

| Target | Role |
| --- | --- |
| Native 7z header parse | Deep coverage of the pure-Python header parser |
| 7z `open_archive` + member list/materialize | Reader/spine path after parse |
| `detect_format` over arbitrary/prefix seeds | Magic/peek-replay entry point |
| ZIP and TAR `open_archive` + member list | Shallow wrapper/translation coverage |
| ISO `open_archive` + member list | Shallow; MUST use a hard wall-clock kill timeout |
| RAR metadata parse (+ open/list when registered) | Scaffold now; skip cleanly until the backend is available |

Full member **extract** is out of scope for this harness (covered by the
mutation harness). Stream/codec-only targets MAY be added later without removing
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
| Push to `main` | Fuzz workflow runs partitioned ~120s budget; green if no crash/hang/raw exception |
| `workflow_dispatch` with longer env budget | Same targets; extended exploration |
| Pull request (default matrix) | Atheris job not required |
| RAR backend absent | RAR target skipped; other targets still run |
| Fuzzer finds a crashing input | Job fails; repro bytes uploaded; re-run command printed |
| Mutation harness / `ARCHIVEY_FUZZ` | Still available and unchanged in role |
