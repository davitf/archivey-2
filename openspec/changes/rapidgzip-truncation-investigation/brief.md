# rapidgzip-truncation-investigation — pin down gzip truncation handling

**Status:** Exploration — this is an investigation plus specs, not an implementation yet. One task of eleven done. Depends on nothing. Not breaking. Effort: small once the measurements are in.

**Why it matters:** when gzip is read through the rapidgzip accelerator, rapidgzip does not reliably report truncation — it raises for some cuts but silently returns short or zero output for others. Archivey currently backstops this by comparing the decompressed length against the gzip size trailer, but that backstop is a heuristic built on incomplete knowledge. It only handles single-member files, its multi-member disambiguation can miss a truncation, and — most importantly — maintainer testing suggests rapidgzip actually reports almost all truncations itself, and stayed silent only for a file that was exactly a bare ten-byte header with no payload. If the silent case is really that narrow, the current backstop is far more machinery than the problem warrants.

**What it does:** characterizes rapidgzip's truncation and corruption behavior precisely — across input sizes, member counts, and cut points, on both Linux and macOS — then decides whether to keep a narrowed backstop, extend it to multi-member files with a safe size comparison, or remove it in favor of rapidgzip's own errors plus a small special case.

**Decided:** the direction is settled as "measure first, then choose"; the spec already states the backstop should be the narrowest check that covers the genuinely-silent cases without ever false-flagging a valid file. A low-risk interim fix already landed — the multi-member header scan no longer reads the whole file into memory.

**Your call later:** which of the three outcomes — narrow, extend, or remove — after seeing the measurements.

**Bottom line:** a scoped investigation that will likely let us shrink or delete a fragile heuristic; low urgency, not a release blocker.
