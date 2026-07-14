# stored-digest-dedupe-parity — surface cheap stored hashes for dedupe, everywhere

**Status:** Complete — all ten tasks done; ready to archive. Depends on nothing. Not breaking (purely additive). Effort: small.

**Why it matters:** the founding use case is indexing and de-duplicating decades of backups, and the vision calls out using hashes the archive already stores without decompressing. Coverage is currently uneven: 7z, ZIP, and RAR5 surface their stored checksums, but single-file gzip and lzip do not — even though both store a checksum of the decompressed content in their trailer that is cheap to read. There is also no written policy for which format surfaces which digest, so parity can drift silently.

**What it does:** surfaces the gzip and lzip trailer checksum as the member's stored crc32 when it is cheaply readable, documents a cross-format stored-digest matrix with a cheap-dedupe recipe, and adds a conformance-sweep assertion so parity is regression-gated.

**Decided:** only surface the checksum when it is free — a seekable source, and for gzip only a single-member file, since a multi-member gzip trailer covers just the last member. Never force a decompression pass. This is metadata only; it does not change how reads or verification behave. Formats with no cheap whole-member stored digest, like bzip2 and tar, correctly show none.

**Your call later:** whether lzip's checksum is surfaced only through the seekable backend or also via a cheap trailer peek — decide with the same seekable-source gate already used for size.

**Bottom line:** small, additive, and squarely the reason the library exists; best shipped complete rather than backfilled once users notice the gaps.
