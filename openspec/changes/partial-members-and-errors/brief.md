# partial-members-and-errors — recover listing prefix plus an honest error

**Status:** Ready to implement. Depends on Option F TAR end-of-archive behavior already in tree. Blocks closing api-coherence Q7 and the VISION damaged-input listing gap for random-access callers. Not a package-default break; random-access iteration on damaged archives changes from fail-closed-before-any-yield to yield-then-raise. Effort: medium.

**Why it matters:** VISION says damaged input should yield recoverable members and an honest error. Option F made the TAR end error honest, but random-access members and iteration still throw away the recovered prefix. Only streaming already shows both.

**What it does:** Adds a MemberListReport from list_members that always returns recovered members, an optional terminal error, and diagnostics. Keeps members and scan_members as complete-or-raise. Aligns random-access iteration and stream_members with streaming: yield the prefix, then raise. Never publishes a partial list as a successful complete cache. CLI list prints the prefix and exits one when the report carries an error. Random-access extract prep stays fail-closed.

**Decided:** Dual listing surface beats diagnostics-only or a members keyword argument. Resource limit errors stay raise-only on listing APIs. Salvage, soft extract, and the verify primitive stay out of scope. First consumer is the TAR Option F paths; incomplete members stay identity-usable for open by member object.

**Your call later:** Exact public method name if list_members feels wrong beside scan_members; whether MemberListReport should iterate like ExtractionReport (design leans yes); re-scan versus replay when list_members is called again after an incomplete random-access pass on a seekable source.

**Bottom line:** Spec-ready Q7 fix so inventory can see both sides of a damaged archive without lying that the listing was complete.
