## Context

> **Depends on `concurrent-open-opt-in`.** Interleaving multiple open member streams is an
> opt-in, format-uniform capability (`allow_multiple_open_streams`); the default still permits
> one live stream at a time and raises on a second overlapping open. That change owns the
> `archive-reading` rewrite (opt-in gate + dropping the TAR-RA exemption). Everything below is
> the **TAR + ISO mechanism** that runs when the caller has opted in (or whenever member
> streams are handed out — see D5).

| Layer | `read()` behavior | Lock? |
|---|---|---|
| `tarfile._FileInFile` | seek then read on shared `fileobj` | No |
| `pycdlib.PyCdlibIO` | seek then read on shared `_fp` | No |
| `zipfile._SharedFile` | seek then read under `ZipFile._lock` | Yes |
| `SharedSource` views | seek then read under source lock | Yes |

TAR and ISO match each other. Single-threaded interleaved `read()` already re-seeks; the gap
vs ZIP is a lock held across the library's whole `read()`. Routing TAR through
`SharedSource.view(offset_data, size)` would reimplement sparse — rejected for now.

## Goals / Non-Goals

**Goals:**

- TAR-RA and ISO support interleaved concurrent member streams under the opt-in.
- Preserve tarfile sparse / pycdlib extent logic unchanged.
- One small streamtools primitive reusable by both backends.

**Non-Goals:**

- Owning the `allow_multiple_open_streams` gate (`concurrent-open-opt-in`).
- Native TAR reader / SharedSource-at-`offset_data`.
- Streaming TAR concurrent open.
- Making `ArchiveReader` thread-safe for concurrent `open()` / `close()`.

## Decisions

### D1. Locked member-stream wrapper (not lock-per-op on the raw FD)

**Choice:** Wrap the **member** stream from `extractfile` / `open_file_from_iso`. Each
data-path `read` / `readinto` acquires a **per-archive** lock for the duration of the inner
call. Locking the underlying FD's `seek` and `read` separately does **not** make seek+read
atomic across threads.

### D2. One lock per reader instance

**Choice:** `TarReader` and `IsoReader` each own one `threading.Lock` shared by every
wrapped member stream from that archive.

### D3. Keep using `extractfile` / pycdlib for data

**Choice:** Do not bypass the libraries for member bytes. Sparse TAR stays stdlib's problem.

**Alternative rejected:** SharedSource views at `offset_data` / ISO extents.

### D4. Compliance path under the opt-in

Archivey-owned byte-range backends still use SharedSource views. Library-owned
seek-before-read backends (TAR, ISO) use this lock wrapper. ZIP already has `_SharedFile`.

### D5. Apply the wrapper whenever RA member streams are handed out

**Choice:** Always wrap TAR-RA / ISO member data streams. Uncontended lock cost is negligible;
the opt-in gate still prevents a second overlapping open unless the caller asked for it.
Optional "wrap only when opted in" is fine if cheaper, but not required.

### D6. Streaming TAR

Unchanged; forward-only.

## Risks / Trade-offs

- **[Risk] Buffering bypasses the lock** → place the wrapper so `_wrap_member_stream` /
  buffers call into the locked layer, not the raw library stream.
- **[Risk] Catalog I/O vs data** → materialize-before-fan-out; do not catalog concurrently
  with member reads.
- **[Risk] False sense of reader thread-safety** → lock only serializes member-stream I/O;
  concurrent `reader.open()` remains unsupported without the opt-in's tracking.
- **[Trade-off]** Depend on libraries continuing seek-before-read (same class of dependency
  ZIP has on `_SharedFile`).

## Migration Plan

1. streamtools wrapper + unit tests.
2. Wire TAR-RA and ISO.
3. format-tar / format-iso deltas; parallel-reader audit rows.
4. Opt-in interleave tests (after / with `concurrent-open-opt-in`).

## Open Questions

- Exact wrapper class name.
- Whether backends wrap before `_wrap_member_stream` (prefer yes).
