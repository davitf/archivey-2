# decide-strict-archive-eof-default — pick the TAR end-of-archive strictness stance

**Status:** Blocked on a decision. Depends on nothing. Blocks honest user Gotchas copy for TAR silent-shorten, and any default flip in config. Breaking only if Options B, C, or E win. Effort: small for Option D docs and cross-links; medium if the library default or extract semantics change.

**Why it matters:** The founding inventory use case needs honest listings, but stdlib tarfile can treat a corrupt mid-archive header as a clean end. Archivey’s trailer check is the only backstop, and today it warns by default. Flipping that default is a product stance, not a drive-by fix — Phase 5 already chose warn-by-default for trailer-less real-world tars.

**What it does:** Parks Options A through E with trade-offs, provisionally specs the recommended path (keep default false, teach the opt-in, CLI strict wedge via cli-v1), and lists apply tasks once the maintainer locks a choice.

**Decided:** Native TAR is out of scope here; one bool cannot yet separate missing trailers from corrupt-shortened listings. Options B and E are rejected for v1 unless the maintainer overrides. Provisional specs and docs assume Option D.

**Your call later:** Which option, A through E? If D, should archivey test default to strict end-of-file or only expose a flag? If C, split on the streaming flag or on source seekability? If E, how does extract report archive-level end-of-file?

**Bottom line:** Read design.md, pick an option when ready; until then leave the library default alone and do not invent Gotchas wording that assumes a flip.
