# Design — `required_source` on `FormatAvailability`

## Decisions

### The field's shape: an ordered minimum, not a set and not a boolean

Three shapes were considered for "can this format be read from a pipe?".

| Shape | Why not |
|---|---|
| `streams_from_pipe: bool` | Ages badly. The day a third source shape exists (a bounded-but-not-seekable view, say) it needs a *second* boolean that has to be kept consistent with the first. |
| `sources: frozenset[StreamCapability]` | Can express states no real format has — `{SEEKABLE}` without `FORWARD_ONLY` is a format that needs seeking, which is fine, but `{FORWARD_ONLY}` without `SEEKABLE` is a format that *refuses* a file, which does not exist. A set makes nonsense representable. |
| **`required_source: StreamCapability`** | **Chosen.** An ordered minimum can only express the real thing, extends by adding an enum member rather than a field, and reuses the vocabulary the cost receipt already publishes on its source-shape axis — so the caller's test is `fmt.required_source <= reader.cost.stream_capability`, one comparison between two values of the same type, rather than a lookup table mapping one vocabulary onto another. |

### `StreamCapability` had no ordering, and the decision requires one

The resolution that picked this shape (`review/simplicity-consistency/open-questions-for-discussion.md`
§O4) asserts that "`StreamCapability` is **ordered** — `FORWARD_ONLY` is weaker than
`SEEKABLE`". It was not: `StreamCapability` is a plain `Enum` with two string members and
no comparison operators, so `required_source <= cost.stream_capability` raised
`TypeError`. The ordering is therefore part of *this* change rather than something it
relies on.

The order is not a judgement call — there is exactly one direction that can hold. A
seekable source can serve every read a forward-only one can, so `FORWARD_ONLY` is the
weaker requirement and sorts first. `functools.total_ordering` over an explicit
weakest-first tuple keeps the definition to one line and the ordering to one place.

Ordering is added to the enum itself rather than as a `satisfies()` helper on
`FormatAvailability` because the relation is a property of the two source shapes, not of
the availability report: `reader.cost.stream_capability` deserves the same comparison, and
a helper on one side would not give it.

### `required_source` is derived, not declared a second time

`ReadBackend.SUPPORTS_STREAMING_NON_SEEKABLE` already records exactly this fact — it is
the flag `open_archive` consults to decide whether a non-seekable source is accepted. The
registry maps it (`True → FORWARD_ONLY`, `False → SEEKABLE`) rather than adding a parallel
per-backend constant, so the queryable answer cannot drift from the enforced one.

### Formats with no registered backend

`format_availability()` answers for any `ArchiveFormat`, including one with no backend
registered at all (support `NONE`, empty `missing`). There is no honest source shape for a
format nothing can read, so the field defaults to `SEEKABLE` — the conservative answer,
which sends a caller to buffer rather than to a refusal at open. The dataclass default
also keeps the three-positional-argument construction used across the registry valid.

### The directory format is `SEEKABLE`, and that is deliberate

A directory source is a filesystem path, not a stream, so "the weakest source shape" is
strictly speaking a category error for it. Deriving from
`SUPPORTS_STREAMING_NON_SEEKABLE` still gives the correct caller-facing answer — you
cannot hand the directory backend a pipe — and the alternative (a `None` for "not
applicable") would force every caller to handle a third state to answer a two-state
question.
