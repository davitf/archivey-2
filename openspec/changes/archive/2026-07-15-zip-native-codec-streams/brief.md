# zip-native-codec-streams — decode ZIP through our own codec layer

**Status:** Ready to implement, with two framings to confirm against real fixtures. Depends on nothing; it is the foundation the ZIP AES change builds on. Not breaking. Effort: medium.

**Why it matters:** ZIP member decompression currently goes through the standard library's zipfile, which only decodes deflate, bzip2, and lzma. But archivey's codec layer already supports Deflate64, Zstandard, and PPMd, and the registry already advertises those for ZIP — so today a Deflate64, Zstd, or PPMd ZIP member fails, because the advertised codecs are never actually reached. This widens ZIP compatibility beyond stdlib, which the maintainer wants in the first release.

**What it does:** keeps stdlib zipfile for parsing the central directory and listing, but reads each member's raw compressed bytes itself — via a small, bounded local-header parse and a slice — and decodes them through the shared codec layer, with unified verification and error translation. It is also the first step toward a fully native ZIP parser later.

**Decided:** the extended codecs are backend-gated, so a missing backend raises the same "package not installed" error as every other format, rather than stdlib's not-implemented error; a corrupt body raises the standard corruption error; encrypted members deliberately stay on the existing zipfile path for now — only unencrypted members move to the codec layer in this change.

**Your call later:** confirm ZIP's PPMd parameter-header framing and Zstd framing match what our backends expect, using a real producer; and decide whether Deflate64 for ZIP reuses the 7z extra or gets promoted so "ZIP compatibility" doesn't read as "install the 7z extra."

**Bottom line:** unlocks real-world ZIP codecs stdlib can't touch, and sets up ZIP AES next.
