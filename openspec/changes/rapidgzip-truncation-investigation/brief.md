# rapidgzip-truncation-investigation — pin down gzip truncation handling

**Status:** Ready to implement after this enrichment lands. Investigation + specs; one task of eleven done (bounded-memory multi-member scan). Depends on nothing. Not breaking. Effort: small once the measurements are in. **Pre-0.2.0 pay item** (debt-ledger Q4).

**Why it matters:** when gzip is read through the rapidgzip accelerator, rapidgzip does not reliably report truncation — it raises for some cuts but silently returns short or zero output for others. Archivey currently backstops this by comparing the decompressed length against the gzip size trailer, but that backstop is a heuristic built on incomplete knowledge. It only handles single-member files, its multi-member disambiguation can miss a truncation, and — most importantly — maintainer testing suggests rapidgzip actually reports almost all truncations itself, and stayed silent only for a file that was exactly a bare ten-byte header with no payload. If the silent case is really that narrow, the current backstop is far more machinery than the problem warrants.

**What it does:** characterizes rapidgzip's truncation and corruption behavior precisely — across input sizes, member counts, and cut points, on both Linux and macOS — then decides whether to keep a narrowed backstop, extend it to multi-member files with a safe size comparison, or remove it in favor of rapidgzip's own errors plus a small special case.

**Decided:** measure first, then choose; backstop = narrowest check covering silent cases without false-flagging valid files. Interim memory fix landed (#14). Debt-ledger **Q4 = PAY before 0.2.0** (2026-07-20): do not ship the under-characterized heuristic as release-done; implementation is a later PR. See `design.md` for AUTO/ISIZE coupling, the two length mechanisms, fuzz-off constraints, and adjacency notes.

**Your call later:** which of the three outcomes — narrow, extend, or remove — after seeing the measurements.

**Bottom line:** scoped investigation likely to shrink or delete a fragile heuristic; required before 0.2.0, but not started in the triage PR that recorded Q4.
