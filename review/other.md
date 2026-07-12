# Theme 8 — Anything I didn't think to ask

Things outside the eight themes that stood out while reading.

## O-1 — Importing the ISO backend mutates pycdlib process-globally (VERIFIED, by-design, flag it)

`iso_reader.py` installs `_install_pycdlib_directory_cycle_guard()` **at module import**
(`:169`), which does `setattr(pcd_module, "collections", _DequeGuardedCollections(...))`. This is
process-global and permanent: any *other* code in the same process that uses pycdlib directly now
gets archivey's guarded `deque` too. It's well-documented and confined to pycdlib's namespace (not a
global `collections.deque` swap), and it fixes a real infinite-loop-on-hostile-ISO bug. But it's a
side effect of `import archivey.internal.backends.iso_reader` — which `import archivey` triggers
eagerly (`__init__.py:195`). A consumer embedding archivey alongside their own pycdlib usage should
know their pycdlib is being patched. Worth one line in the ISO docs / a note in the threat model.
Not a bug; a "the maintainer should be aware" item.

## O-2 — Security posture is genuinely good (positive finding)

No `eval`, `exec`, `pickle`, `marshal`, `subprocess` with shell, or `os.system` anywhere in the
core read path (the only subprocess is the planned `unrar` data pipe, delegated cleanly). The AES
path goes through one wrapped backend. The extraction path is defense-in-depth (three symlink
layers, atomic writes, bomb guards). For a library whose whole pitch is safety, the implementation
matches the marketing — which is not always true. Credit noted so it's on the record alongside the
gaps.

## O-3 — `ArchiveMember` mutability is a deliberate sharp edge worth a lint (VERIFIED, low)

`ArchiveMember` is mutable and `__hash__` raises (types.py:347). The spec says "callers must treat
as read-only." Late-bound fields (`link_target_member`, `_diagnostics`, `link_target`) are mutated
by the library *after* the member is handed to the caller (during lazy link resolution / streaming
finalization). A caller who snapshots `member.link_target` early and one who reads it after a later
`scan_members()` can see different values for the *same object*. This is documented, but it's the
kind of thing that bites a user building a cache keyed on member identity. A `member.freeze()` /
immutable-view helper, or at least a doc example of the "read it when you need it, don't cache
across passes" rule, would help. (Related: `member_id`/`archive_id` raise `AttributeError` before
registration — a caller touching them on a hand-built member gets a surprise.)

**Reply to the maintainer's question ("allow hashing via the immutable fields?"): keep it
unhashable; add an explicit identity key instead.** The blocker is the hash/eq contract, not just
mutability: `ArchiveMember.__eq__` is the dataclass field comparison (name/type/size/… — it
*excludes* `hashes`/`extra`/`link_target_member`). Python requires `a == b ⇒ hash(a) == hash(b)`.
So a hash derived from the *identity* fields (`archive_id`, `member_id`) would be **inconsistent**
with that value-based `__eq__`: two distinct members with identical metadata are `__eq__`-equal but
would hash differently — a latent "vanishes from a set" bug. The only ways to make hashing sound are
(a) redefine `__eq__` to identity — breaking the useful value-equality — or (b) hash the mutable
fields — the classic mutable-key trap the class already avoids. Both are worse than unhashable.

Recommendation: leave `__hash__` raising, and give callers the key the library itself uses
(`selection.py` keys by `(archive_id, member_id)`): a public `member.key` property returning
`(archive_id, member_id)` (raising the same clear error before registration), plus a docs example
of `by_name[m.name]` / `seen.add(m.key)`. That gives set/dict use without any of the contract
hazards. (Not implemented here — it's a public-API addition worth its own small change, and it
wasn't among the approved fixes.)

## O-4 — The diagnostics collector's escalate-under-IGNORE path (SUSPECTED, low)

`diagnostics_collector.py:219-234`: when disposition is `IGNORE`, `should_deliver` is False, so
retention/log/callback are skipped — but if `escalate_as` is set (strict-EOF TAR), the terminal
`raise escalate_as(...)` still fires. So a user who sets `strict_archive_eof=True` *and* policies
`ARCHIVE_EOF_MARKER_MISSING → IGNORE` gets a `TruncatedError` with no accompanying diagnostic
recorded. Probably fine (strict-EOF is a hard "I want this to raise" signal that outranks the
per-code policy), but the interaction of the two knobs isn't specced and could surprise. Worth a
sentence in the diagnostics spec, or a test pinning the intended precedence.

## O-5 — `_infer_member_name` default `"data"` can collide (VERIFIED, very low)

`single_file_reader.py:67-75`: a stream source with no name yields member name `"data"`; a named
source strips the compression extension. Two `open_stream`/`open_archive` calls on nameless streams
both produce a member named `"data"` — fine in isolation, but a caller extracting several such
one-member archives into one directory gets collisions. Cosmetic; the caller controls dest naming.
Noting for completeness.

## O-6 — Good bones I'd protect in review

- The `error-handling` "no catch-all, return None to propagate" rule is followed with unusual
  discipline — every translator maps a closed set. This is the single most important invariant for
  "we don't hide bugs," and it holds. Guard it hard in code review.
- The cost model's three orthogonal axes (listing / access / stream) are conceptually right and
  consistently populated (the directory `INDEXED` question aside).
- The `streamtools` dependency-free boundary is real and worth keeping — it's what would let the
  stream layer be extracted or reused, and it forces clean interfaces at the codec boundary.
