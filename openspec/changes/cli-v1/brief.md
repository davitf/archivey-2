<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# cli-v1 — Hybrid archivey command for list, test, extract, and info

**Status:** Mostly ready to implement the scaffold and read-side verbs; a few parser UX choices remain open. Depends on cross-platform-name-safety so rename-on-collision exists for extract. Blocks the public release demo wedge. Not breaking for published users (pre-release). Effort: medium.

**Why it matters:** Reading is done; the missing wedge is the safer unzip people can try in ten seconds, plus the maintainer inspection tool. The old cli spec was too thin for inspect, verify, policy-aware extract, and detect.

**What it does:** Adds the archivey command as a hybrid CLI: subcommands plus short aliases, defaulting to list. Ships list, test, extract, and info (detect is an alias). Reserves salvage and future hash, create, and convert without implementing them. Progress bars stay behind the cli extra; the command itself installs with the base package.

**Decided:** Default verb is list. List shows a clean layer-one view; digests are opt-in. Extract exposes strict, standard, and trusted policies, defaulting to strict, and defaults overwrite to rename while the library stays on error. Destination is always minus-d or dest, defaulting to the current directory, so filters never compete with an output path. Salvage is a visible reserved flag that errors. Stay on argparse so the base install stays zero-dependency; Click would force a core dependency or a gated command.

**Your call later:** Whether include and exclude flags are needed beyond positional filters, exit codes, multi-archive and stdin in version one, and how chatty test should be. Hash emit formats stay deferred.

**Bottom line:** Grammar for extract is settled; finish the small open list, then implement after name-safety lands.
