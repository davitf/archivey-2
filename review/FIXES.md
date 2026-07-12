# Direct fixes applied

Each is one commit. Threshold honored: only doc/comment fixes where the code is clearly right and
no public API, behavior, or concurrency mechanism was touched. Everything heavier is written up in
the theme reports + QUESTIONS.md instead.

| # | File:line | What | Why | Commit |
|---|-----------|------|-----|--------|
| 1 | `src/archivey/core.py:272` | `open_stream` docstring summary: "return the decompressed bytes" → "return a decompressing stream" | The function returns an `ArchiveStream` (a lazy `BinaryIO`), not bytes; the rest of the docstring already describes stream behavior. Doc-only. Tests: `test_open_stream.py` green. | "Fix open_stream docstring: it returns a stream, not bytes" |
| 2 | `src/archivey/reader.py:122` | `ArchiveReader.open` docstring: foreign-member open raises `ValueError` → `ArchiveyUsageError` | `BaseArchiveReader.open` raises `ArchiveyUsageError` (confirmed by `test_reader_contract.py` "does not belong to this reader"). `ArchiveyUsageError` is deliberately outside the `ArchiveyError` tree, so the wrong type could mislead a caller's `except`. Doc-only. Tests: `test_public_api.py`, `test_reader_contract.py` green. | "Fix ArchiveReader.open docstring: identity mismatch raises ArchiveyUsageError" |

## Considered but deliberately NOT applied

- **Populate ZIP `member.hashes["crc32"]`** (specs-docs S1 / latent-bugs L3): spec-mandated and
  one line, but it's a public data-model behavior change with a VerifyingStream design question
  attached → QUESTIONS Q3.
- **`except Exception` → `except BaseException` in `_get_members_registered`** (concurrency C1 /
  latent-bugs L2): correct fix, but it touches a concurrency mechanism → QUESTIONS Q1.
- **Bound `num_files` in the 7z parser** (latent-bugs L1): security-relevant hostile-input parser;
  the right bound needs a decision → QUESTIONS Q4.
- **Directory `_MEMBER_LIST_UPFRONT` / `INDEXED` cost** (specs-docs S2/S3): spec-vs-spec conflict,
  must be decided, not silently resolved → QUESTIONS Q2.
