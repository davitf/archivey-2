# Brief: stop-on-failure-not-policy

**Status.** Ready to implement. The direction is decided — you endorsed it on pull request
one-six-three — and the one thing left open, the name of a future strict-security mode, is
deliberately out of scope and does not block this change.

**Why it matters.** Today the library's error policy treats two very different things the
same way. If you ask it to stop on error, it stops both when a member is genuinely broken
and when the safe-extraction policy refuses an unsafe member. But refusing an unsafe member
is the library doing its job, not an error. So stop-on-error aborts an otherwise-good
archive the moment one entry looks dangerous — the opposite of "skip the bad one and keep
going", which is the whole point of the library.

**What it does.** It narrows the error policy so it governs member failures only. A policy
block is always recorded and always lets extraction continue, whether or not stop-on-error
is set. Genuine failures, like corrupt or truncated data, still stop the run under stop
mode. All the hard safety limits — resource guards, bomb guards, keyboard interrupt — keep
halting exactly as before. The change lives at a single spot in the extraction handler,
with no new options and no signature change.

**Decided.** Stop keys off failures, not policy. Aborting the whole archive on any unsafe
member becomes a separate future opt-in, not part of the error policy.

**Your call later.** Whether that future strict mode is a policy setting or a command-line
flag — a separate change.

**Bottom line.** This is a breaking change to the default stop behavior, and it also
settles the command-line exit-code question on pull request one-six-three: because stop
never halts on a block anymore, the awkward case simply disappears.
