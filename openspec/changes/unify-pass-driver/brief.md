# unify-pass-driver — one pass driver and one member-list finalize path

**Status:** Ready to implement after proposal review. Depends on nothing. Blocks nothing. Not breaking if the suite stays green. Effort: medium to large.

**Why it matters:** Native RAR added a fourth copy of the stream-members close-previous loop, exactly as the old simplification review predicted. The member-list pipeline still has two drive loops and two mirrored double-fault guards. Shipping 0.2.0 with that debt fights the zero-debt goal; the maintainer chose to pay it now rather than gate the next backend.

**What it does:** First widen mutation fuzz over static solid RAR fixtures. Then collapse link finalization into one helper, and replace the four hand-rolled pass-stream loops with one shared driver that backends customize via hooks.

**Decided:** Pay before 0.2.0, not an entry gate. TAR keeps no previous-close as an explicit flag. The shared driver always closes the last stream in its `finally` (all backends — no `leave_last_open`); resource cleanup runs after that close. No public API or OpenSpec requirement deltas if behavior is preserved. Do not teach the declarative RAR corpus builder solid mode in this change.

**Your call later:** Helper naming is implementer choice. Whether wave-one mutation also includes symlink or file-version solid fixtures, or only the two basic solid archives.

**Bottom line:** Internal cleanup with a solid-RAR mutation safety net first; review proposal and design, then implement.
