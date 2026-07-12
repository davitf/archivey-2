# Vendored RAR fixtures for the native reader tests

Most archives here are **generated** by:

```bash
uv run python scripts/gen_rar_fixtures.py
```

That script shells out to RARLAB `rar` (and, when the system `rar` is 7.x and
lacks `-ma4`, downloads a pinned RAR 6.24 linux-x64 binary into the user cache
solely to write RAR4 fixtures). Re-run the script after changing member layouts
or compression flags, then commit the updated binaries.

## Legacy (not regenerated)

| File | Provenance |
| --- | --- |
| `rar15-comment.rar` | Copied from [markokr/rarfile](https://github.com/markokr/rarfile) `test/files/` (ISC) |
| `rar202-comment-nopsw.rar` | Same |

Modern `rar` cannot emit RAR 1.5 / 2.0; keep these as-is.
