<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# cli-v1 — Hybrid archivey command for list, test, extract, and info

**Status:** Blocked on a decision for extract defaults; the rest of the shape is settled enough to implement the scaffold and read-side verbs. Depends on cross-platform-name-safety so rename-on-collision exists for extract. Blocks the public release demo wedge. Not breaking for published users (pre-release). Effort: medium.

**Why it matters:** Reading is done; the missing wedge is the safer unzip people can try in ten seconds, plus the maintainer inspection tool. The old cli spec was too thin for inspect, verify, policy-aware extract, and detect.

**What it does:** Adds the archivey command as a hybrid CLI: subcommands plus short aliases, defaulting to list. Ships list, test, extract, and info (detect is an alias). Reserves salvage and future hash, create, and convert without implementing them. Progress bars stay behind the cli extra; the command itself installs with the base package.

**Decided:** Default verb is list. List shows a clean layer-one view; digests are opt-in. Extract exposes strict, standard, and trusted policies, defaulting to strict. Salvage is a visible reserved flag that errors. Info summarizes format identity without dumping members. Grammar avoids tar-style minus-c for check so create can use it later.

**Your call later:** Default overwrite for extract (error versus rename), destination grammar, pattern syntax, exit codes, multi-archive and stdin in version one, and how chatty test should be. Hash emit formats stay deferred.

**Bottom line:** Core shape is pinned; answer the extract overwrite default next, then implement after name-safety lands.
