# sevenzip-header-cursor-parse — parse the 7z header from a byte cursor instead of a stream

**Status:** Ready to implement. Depends on nothing. Blocks nothing. Not breaking. Effort: medium.

**Why it matters:** After the recent listing work fixed the 7z name byte-loop, seven-zip open-and-list is still about two times its native peer, and the biggest remaining chunk is the per-field header parse. The seven-zip parser walks a header that is already fully in memory, but it does so through a BytesIO stream: every small field is a stream read, every property gets its own BytesIO wrapper, and truncation checks used to seek back and forth. There is no actual input-output happening down there, so the stream machinery is pure overhead. The RAR parser already does this the fast way, reading each block into a bytes buffer and walking it with an integer position. Seven-zip is the last native parser still on the slow idiom.

**What it does:** It replaces the in-memory BytesIO parsing with a small byte cursor over a memoryview of the header, converting every header-parsing helper but leaving the file-level signature read on a real stream. No public behavior, format support, or dependency changes.

**Decided:** ZIP and TAR are out of scope, because they hand parsing to the C standard library and have no equivalent loop to speed up. RAR is already cursor-based. Only the format-agnostic primitives are worth sharing; the seven-zip and RAR variable-length integer encodings differ, so those stay per-format. Hot per-member reads keep the position in a local, RAR-style, rather than paying for a stateful class on every field. Every hostile-input bound is preserved, and that guarantee is now written into the seven-zip spec as representation-independent.

**Your call later:** None blocking. The one thing to settle during implementation is the exact reader shape, class versus free functions, which the existing listing probe decides by measurement.

**Bottom line:** A clean, self-contained parser refactor that removes a known overhead source; worth doing whenever seven-zip listing speed is a priority, with the probe census as the accept gate.
