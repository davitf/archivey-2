# rapidgzip-deflate-zlib-acceleration — accelerate raw deflate and zlib streams with rapidgzip

**Status:** Ready to implement, with one value to benchmark. Depends on nothing; the ZIP native-codec-streams change benefits from it but does not block it. Not breaking. Effort: small to medium.

**Why it matters:** rapidgzip, which archivey already ships as the seekable gzip and bzip2 accelerator, turns out to also decode raw deflate and zlib natively as of version zero-point-sixteen. Today the deflate and zlib codecs always fall back to standard-library zlib, so seekable deflate and zlib streams get no parallel decode and no real random access. That includes the ZIP deflate members the native-codec change will route through the deflate codec.

**What it does:** It adds a rapidgzip path to the deflate and zlib codecs, gated exactly like gzip, and passes the stream through unwrapped because rapidgzip auto-detects the format. No fake gzip wrapper is needed. The default sequential backend stays standard-library zlib.

**Decided:** No gzip wrapping. The input handed to rapidgzip must be exactly bounded, since it over-reads past the deflate end looking for another member. Standalone zlib and deflate lose standard-library's truncation detection because rapidgzip does not check the zlib checksum and can return a silent short read; container members stay safe because the container's own CRC catches this. And auto mode gains a minimum-input-size gate so tiny members do not pay rapidgzip's setup cost.

**Your call later:** The exact size threshold — chosen from a benchmark across compressed sizes on Linux and macOS — and whether that gate lives in the accelerator-mode helper or at the codec call sites.

**Bottom line:** A small, low-risk extension of existing gzip acceleration to the whole deflate family; land it alongside or just after the ZIP change.
