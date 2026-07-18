# partial-members-and-errors — recover listing prefix plus an honest error

**Status:** Ready to implement. Depends on Option F TAR end-of-archive behavior already in tree. Blocks closing api-coherence Q7 and the VISION damaged-input listing gap for random-access callers. Not a package-default break; random-access iteration on damaged archives changes from fail-closed-before-any-yield to yield-then-raise; get_members_if_available is renamed and returns a report. Effort: medium.

**Why it matters:** VISION says damaged input should yield recoverable members and an honest error. Option F made the TAR end error honest, but random-access members and iteration still throw away the recovered prefix. Only streaming already shows both.

**What it does:** Adds members_report returning a MemberListReport. Materialization stores one immutable report (completeness is error is None) so yield-then-raise and complete-or-raise both derive from it. Renames get_members_if_available to members_report_if_available returning MemberListReport or None. Aligns random-access iteration with streaming. CLI list prints the prefix and exits one when the report carries an error. Random-access extract prep stays fail-closed.

**Decided:** Dual listing surface with a single stored report. members_report_if_available peeks at that report (complete or incomplete) without scanning. Exception-carried recovered members rejected. Resource limit errors stay raise-only. Replay the stored report on repeat calls. MemberListReport iterates like ExtractionReport. Salvage, soft extract, and verify stay out of scope.

**Your call later:** None — the design is settled.

**Bottom line:** Spec-ready Q7 fix so inventory can see both sides of a damaged archive without lying that the listing was complete.
