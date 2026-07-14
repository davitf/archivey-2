# RAR ↔ unrar: can we replace the temp file with pipes?

> Investigation note (2026-07). Origin: "the RAR reader writes stream data to a temp
> file that it then passes to `unrar` — is that bad? `unrar` seems to support stdin;
> could we pipe a small synthetic header + the compressed block instead, like
> `rarfile`'s hack but over a pipe?"
>
> Short answer: **no, and the reason closes off the whole family of pipe ideas.**
> RARLAB `unrar` *seeks* the archive; it cannot read the archive from any
> non-seekable input. Everything below follows from that one measured fact.
>
> Status: not a change proposal. It (a) definitively rules out piping, (b) validates
> two decisions the `format-rar` spec already deferred, and (c) surfaces one genuinely
> new fragility in the solid demux worth a future change. All claims here were measured
> against **RARLAB unrar 7.0.7** on Linux using the repo's RAR fixtures.

Related: [`openspec/specs/format-rar/spec.md`](../../openspec/specs/format-rar/spec.md)
(solid-pipe streaming, deferred small-member optimization),
[`docs/decisions/0002-native-rar-metadata-unrar-data.md`](../decisions/0002-native-rar-metadata-unrar-data.md),
`src/archivey/internal/backends/rar_reader.py`, `rar_unrar.py`.

---

## 1. First, correct the premise: when do we actually write a temp file?

The reader does **not** always spill to a temp file. `unrar` runs against a real path
whenever we have one:

| Source                                   | unrar reads              | Temp file?                          |
| ---------------------------------------- | ------------------------ | ----------------------------------- |
| `Path` (single archive)                  | the real file            | **No**                              |
| `Path` multivolume (`.partN.rar` on disk)| the real sibling files   | **No**                              |
| Non-path stream (`BytesIO`, socket-ish)  | temp `.rar`              | **Yes — whole archive** (`_ensure_archive_path`) |
| Stream volumes (multivolume from streams)| temp dir of `.partN.rar` | **Yes — whole set** (`_materialize_stream_volumes`) |

So the "huge temp file" cost only bites **non-path sources**, and when it bites it is
the *entire* archive (unbounded disk). That is the thing worth attacking. For the
common `open("x.rar")` path there is nothing to fix here.

---

## 2. The load-bearing fact: `unrar` cannot read a non-seekable archive

`unrar` opens the archive by name and seeks it (main header, the end-of-archive block,
central directory, recovery records). Measured every way it could plausibly accept a
stream:

| Invocation                                          | Result        | Meaning                              |
| --------------------------------------------------- | ------------- | ------------------------------------ |
| `cat a.rar \| unrar p -si`                           | **rc 7**      | cmdline error — `-si` is rejected    |
| `cat a.rar \| unrar p -`                             | **rc 7**      | `-` is not "stdin" to unrar          |
| `cat a.rar \| unrar p /dev/stdin` (pipe behind fd 0) | **rc 2**      | fatal — non-seekable fd              |
| `unrar p /proc/self/fd/N` where N is a **FIFO**      | **rc 2**      | fatal — non-seekable                 |
| `unrar p /dev/stdin < a.rar` (**seekable** redirect) | **rc 0** ✅    | works — fd is a seekable regular file|
| `unrar p /proc/self/fd/N`, N = **inherited memfd**   | **rc 0** ✅    | works — anonymous but seekable       |

The `-si[name]` switch in `unrar --help` ("Read data from standard input") is a
**`rar` compressor** switch (feed *data to compress* via stdin). For `unrar` it is a
command-line error. There is no mode in which `unrar` consumes the archive as a stream.

**Consequence:** *"pipe the header, then the compressed block"* is impossible. Not
"slower" or "fragile" — `unrar` refuses the invocation before reading a byte. Every
variant of the idea (single member, solid block, multivolume fragments) dies here,
because all of them still require `unrar` to seek the thing you hand it.

### The one door this leaves open

`unrar` is happy with any path whose *underlying fd is seekable* — including
`/dev/stdin`/`/proc/self/fd/N` backed by a seekable file, and crucially an **inherited
`memfd`** (`os.memfd_create` + `set_inheritable` + `pass_fds`). That is a seekable,
anonymous, in-RAM "file" that never appears in the filesystem namespace and is freed on
close. It is a real alternative to `mkstemp`, but note what it is and isn't:

- It replaces **disk** with **RAM**. It does *not* make the archive smaller.
- It is Linux-only (`memfd_create`).

---

## 3. What `rarfile`'s "hack" actually is (it is not a pipe)

The reference the question alludes to (`rarfile._open_hack_core`) does **not** pipe
anything into `unrar`. It:

1. builds a minimal RAR: `RAR_ID` + a synthesised main header + **the member's own
   header block and compressed data copied verbatim** from the original archive + a
   synthesised end-of-archive block;
2. writes that to a **small temp file** (`mkstemp`);
3. runs `unrar p tmpfile` on it (the pipe in `rarfile` is only `unrar`'s *stdout*).

So even the library that invented the trick uses a temp **file** for the archive —
because of §2. And it guards the trick heavily (`_must_disable_hack`, `HACK_SIZE_LIMIT`):

| `rarfile` disables the hack when…            | Why (confirmed here)                                  |
| -------------------------------------------- | ----------------------------------------------------- |
| member is **solid**                          | needs the LZ window from earlier members (see §4)     |
| member is **split** across volumes           | compressed data isn't in one place (see §5)           |
| archive/member is **encrypted**              | header/data crypto can't be relocated blindly         |
| `file_size > 20 MB`                           | the temp file would stop being "small"                |
| the source is already a real file            | no benefit — `unrar` reads it directly                |

For an in-memory archive it *can't* hack (e.g. solid), `rarfile` falls back to
`membuf_tempfile` — writing the **whole** archive to a temp file. That is byte-for-byte
what archivey's `_ensure_archive_path` already does. We are not behind `rarfile` here;
we are at exactly the same place, minus the small-member optimization it applies on top.

That optimization is what `format-rar` calls the "extract-hack / temp single-file RAR"
and **already defers behind a benchmark gate** (spec §"Support benchmark-gated
small-member optimization"). This investigation doesn't change that verdict; it explains
*why the pipe variant of it can't exist*, so if we ever un-defer it, it must be a small
temp file or a memfd — never a pipe.

---

## 4. Solid archives: the interesting part, and where the hope breaks

Today, solid `_iter_with_data` runs **one** unnamed `unrar p` over the whole archive and
slices the concatenated stdout by each payload member's *uncompressed* size, yielding
`None` for members `is_payload_file()` says unrar won't emit (dirs, symlinks, hardlinks,
copies). The question proposed two escapes.

### 4a. "Extract a solid member on its own" — no

A single-member synthetic archive (rarfile-style) built from a **solid** member fails:

```
HACK1  file1.txt                    rc=3   (data error)
HACK1  subdir/file2.txt             rc=3
HACK1  implicit_subdir/file3.txt    rc=3
```

The LZ window carries across the solid group; member *N* is undecodable without decoding
`0..N-1`. So **random per-member access into a solid group is impossible** without
replaying the prefix — which is exactly why the spec models solid access as `SOLID`
cost. "Use the compressed offsets directly" for random reads has no foundation.

### 4b. "Feed the whole solid block as one member" — needs real header surgery, not concatenation

Concatenating the members' compressed data and reusing one member's header (so the
"file" is bigger than its declared `unpacked_size`) also fails:

```
giant-data, file1 header (usz=13) + file1+file2+file3 packed data:  rc=3
```

`unrar` honours the per-file framing and declared size; you cannot merge solid members
into one logical file by byte-concatenation. Producing a valid "one giant member"
archive would require **re-encoding a RAR5 file header** with the summed `unpacked_size`
(vint + header CRC) — a real implementation surface, not a copy.

### 4c. And even if 4b worked, the demux boundary is not metadata-derivable

The premise was that a single decompressed blob lets us *"use compressed offsets
directly"* and stop guessing which members `unrar` skips. Measurement kills this: what a
member contributes to the stream is **not** predictable from any single stored field,
and RAR3 and RAR5 disagree.

```
RAR5 solid, symlink member:  csz = 0,  usz = 12,  target lives in the HEADER (file_redir)
RAR3 solid, symlink member:  csz = 12, usz = 12,  target lives in the LZ DATA stream (M0)
```

Both formats emit the **same 13 bytes total** for a solid archive whose only real file
is `file1.txt` — the symlinks contribute nothing to `unrar`'s stdout in *either* format,
even when named explicitly on the command line:

```
unrar p symlinks_solid.rar file1.txt symlink_to_file1.txt   ->  13 bytes  (symlink emits nothing)
```

So:

- Keying the demux on `csz > 0` is **wrong for RAR3 symlinks** (`csz>0`, emits nothing).
- Keying it on `usz > 0` is **wrong for RAR5 symlinks** (`usz>0`, emits nothing).
- The only correct predictor is `unrar`'s *semantic* emission rule — "print regular-file
  data; skip dirs, links, copies" — which is what `is_payload_file()` approximates.

The demux is therefore an inherent re-implementation of `unrar`'s output policy. That's
the real fragility the question was circling, and it is **not** removable by switching to
compressed offsets — there is no offset field that encodes "did `unrar` print this."

> New finding worth a future change: the spec already forbids the ALL-pipe demux for
> **mixed-password** nonsolid archives (wrong-password members vanish from stdout and
> desync sizes). The RAR3-vs-RAR5 symlink asymmetry above is the same class of bug for
> the *solid* pipe. `is_payload_file()` happens to get today's fixtures right, but the
> coupling to `unrar`'s emission policy is undocumented and untested against RAR3 link
> members specifically. A hardening change would (1) pin the emission rule per format
> version with fixtures like `symlinks_solid__rar4.rar`, and (2) consider driving
> `unrar` with the explicit payload member list rather than an unnamed ALL pipe, so our
> selection set *is* the emission set by construction.

---

## 5. Multivolume: concatenation doesn't work; `unrar` wants named files

Two measured "can we just concatenate?" tests, both negative:

```
cat part1.rar part2.rar > one.rar ; unrar p one.rar      ->  rc 3, only 839 / 1600 bytes
```

Whole-volume concatenation fails: the per-volume headers/trailers sit in the middle of
the stream and derail `unrar` after the first fragment. (Fragment-level packed data *is*
continuous across a volume boundary — the parser already models it in one concatenated
byte space for stored reads — but you can't hand that continuity to `unrar` without,
again, reconstructing headers.)

And `unrar` finds subsequent volumes **by filename on disk**:

```
part1 as an inherited memfd (/proc/self/fd/N, no sibling name)  ->  rc 3, stops after volume 1
```

The memfd trick from §2 therefore works for single-file archives but **breaks
multivolume**, because there is no `…part2.rar` sibling to discover. This is precisely
why `_materialize_stream_volumes` writes real, correctly-named `.partN.rar` files into a
temp dir. That design is validated, not replaceable by pipes or memfds.

---

## 6. Options, scored against VISION ("no surprises": neither unbounded temp files nor unbounded memory)

For **non-path stream sources** only (path sources already have no temp file):

| Option                                             | Disk        | Memory      | Fragility                  | Verdict |
| -------------------------------------------------- | ----------- | ----------- | -------------------------- | ------- |
| **Pipe archive to `unrar`**                        | —           | —           | —                          | **Impossible** (§2) |
| Whole archive → temp file (**today**)              | whole arc.  | O(1)        | none                       | Baseline; explicit & declared |
| Whole archive → **memfd**                          | none        | whole arc.  | none; Linux-only           | Trades disk→RAM; sideways for huge archives, nice for small |
| **Small synthetic single-member** archive (seekable src, nonsolid/nonsplit/unencrypted, size-capped) → small temp file or memfd | one member | one member | moderate (RAR3+RAR5 header synth) | The `rarfile` hack; already spec-deferred + benchmark-gated |
| Reconstruct solid group as **one giant member**    | group       | group       | high (RAR5 header re-encode) | Not worth it — still O(group) work, no random-access win (§4) |

Key VISION reading: **there is no free lunch for a huge archive delivered as a
non-seekable stream.** `unrar` needs random access to the whole archive, so *something*
must hold the whole (sub)archive somewhere seekable. The honest choices are *where*
(disk vs RAM) and *how much* (whole archive vs one member) — never "stream it through."

---

## 7. Recommendation

1. **Drop the pipe idea permanently.** Record §2 so it isn't re-proposed: `unrar`
   cannot read a non-seekable archive; `-si` is a compressor switch. Any future
   optimization is "smaller/anonymous seekable file," not "pipe."
2. **The only clean win is the already-deferred small-member optimization**
   (spec §"benchmark-gated small-member optimization"), and it only helps **seekable
   non-path sources** with nonsolid/nonsplit/unencrypted members under a size cap. When
   we pick it up, prefer **memfd over `mkstemp`** (anonymous, auto-freed, still bounded
   to one member) and keep the whole-archive temp file as the fallback. This does *not*
   touch solid or multivolume.
3. **Leave solid streaming as one `unrar p` pipe**, but treat §4c as a real hardening
   item: the demux's dependency on `unrar`'s per-format emission policy is currently
   undocumented and only incidentally correct. That's the one piece of this exploration
   that could become its own OpenSpec change (pin the emission rule, add RAR3 link
   fixtures, consider explicit-member invocation).
4. **Multivolume stays as materialized named files** for stream sources (§5). memfd
   can't serve the volume-discovery-by-name requirement.

Nothing here is ready to *build*. Item 3 is the candidate if any of it graduates to a
change proposal.
