<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# cli-v1 — archivey command for list, test, extract, and info

**Status:** Ready to implement the scaffold and read-side verbs — the parser grammar is now fully settled. Depends on cross-platform-name-safety so rename-on-collision exists for extract. Blocks the public release demo wedge. Not breaking for published users (pre-release). Effort: medium.

**Why it matters:** Reading is done; the missing wedge is the safer unzip people can try in ten seconds, plus the maintainer inspection tool. The old cli spec was too thin for inspect, verify, policy-aware extract, and detect.

**What it does:** Adds the archivey command with subcommand verbs, each a plain word with a single-letter alias like x for extract or l for list, never a dashed flag, defaulting to list. Ships list, test, extract, and info (detect is an alias). Reserves salvage and future hash, create, and convert without implementing them. Progress bars stay behind the cli extra; the command itself installs with the base package.

**Decided:** Default verb is list. List shows a clean layer-one view; digests are opt-in. Extract exposes strict, standard, and trusted policies, defaulting to strict, and defaults overwrite to rename while the library stays on error. Destination is always minus-d or dest, so filters never compete with an output path. When you omit the destination, extraction goes into a smart enclosing folder named after the archive, reusing the archive's own top folder when it already has one, so a messy archive never splatters across your current directory. Passing minus-d dot opts back into the classic extract-into-here behavior. Positional patterns are includes; a long-only exclude flag subtracts. One archive per run. Exit codes stay minimal — zero, one, two — aligned with argparse. Test is quiet by default with a summary, chatty under verbose. Stdin is deferred and the dash token is reserved. Passwords prompt on a terminal instead of forcing secrets onto the command line. Salvage is a visible reserved flag that errors. Stay on argparse so the base install stays zero-dependency; Click would force a core dependency or a gated command.

**Your call later:** Whether track-io ships depends on the library exposing an I/O hook — otherwise it is cut rather than monkeypatched. Whether a distinct exit code for safety refusals is worth splitting out now or later. Hash emit formats and a json output mode stay deferred.

**Bottom line:** The grammar is fully settled; implement after name-safety lands, once the track-io question is answered.
