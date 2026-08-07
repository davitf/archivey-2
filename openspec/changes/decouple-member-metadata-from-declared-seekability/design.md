# Design — decouple member metadata from declared seekability

## Decisions

### Two configs, because they answer two different questions

`SingleFileReader` derived one `StreamConfig` and used it for everything. Its `seekable`
field carries the caller's `MemberStreams.SEEKABLE` declaration, and that is correct for
the member stream — declaring seek demand is what selects an indexed decompressor backend
and resolves accelerator `AUTO`. It is *not* correct for a metadata probe, which never
returns a stream to anybody.

So the reader now derives a second, probe-only config:

| Config | `seekable` | Accelerators | Used by |
|---|---|---|---|
| `_codec_config` | the caller's declaration | the caller's `ArchiveyConfig` | every stream handed to a caller |
| `_metadata_config` | whether the **source** is seekable | `OFF` | `_probe_decompressed_size` only |

Accelerators are `OFF` in the probe config because an accelerator exists to make repeated
member reads fast, and a probe reads no member data — engaging one would pay a startup
cost for a value the native index already has. No codec that reaches this probe today has
an accelerator (it is only called from the xz/lzip metadata hook), so this is a guard for
the next one rather than a behaviour change.

### The lzip gate was redundant as well as wrong

`_probe_lzip_index` tested `self._codec_config.seekable` *and* ran inside
`_with_seekable_source`, which already returns `None` without calling the probe on a
non-seekable source. Removing the first leaves the second, which is the check that was
always the real one.

### What this does not do: force a decode

The rule this change lands on is "the source's shape decides", not "get the size
whichever way". Both probes are bounded backward reads — the xz footer plus its index,
and the lzip trailer walk (O(members) seeks, no decompression). Neither decompresses, and
neither runs at all on a pipe. `member.size` on a `.bz2` stays `None` until EOF, exactly
as before, because there is no cheap answer to read.

### Cost, stated

The probe now runs on a plain `open_archive` of a seekable `.xz`/`.lz` where it
previously did not, so those opens pay one extra bounded backward read. That is the same
cost gzip already pays unconditionally for its trailer CRC-32, and it buys the founding
dedupe use case its "hashes without decompression" answer on the default open — which is
the trade `VISION.md` already made for gzip.

## Rejected alternatives

**Harvest lazily on first access to `member.size`.** Would keep the open cheap, but
`ArchiveMember` is a frozen-ish data record that callers pass around and compare; making
one field trigger I/O introduces a lifetime question (what if the source is closed?) for
a bounded read that gzip already performs eagerly. Rejected for the same reason O3
rejected a per-`open()` seekability flag: it trades a measured, bounded cost for new
state.

**Keep the flag as the gate and document it.** This was the status quo, and it is what
the review's F1 argues against: the flag's own documentation says it is about `seek()` on
a member stream, and a caller reading that sentence has no way to guess it also decides
whether `hashes` is populated.
