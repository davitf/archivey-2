# Tasks — Phase 2: Stream layer reorganization

> Run tools through uv: `uv run pytest`, `uv run mypy`, `uv run ruff`.
> Prerequisite: Phase 1 complete (ported DEV source compiling and green).
> Goal: `io_helpers.py` split into cohesive modules; `BinaryIOWrapper`
> simplified; **no behavior change**.

## 1. Create the streams package

- [x] 1.1 Create `src/archivey/internal/streams/` (with `__init__.py`).
- [x] 1.2 `streams/detect.py` — move `RecordableStream` and
      `RewindableStreamWrapper` (used only for format detection).
- [x] 1.3 `streams/slice.py` — move `SlicingStream`.
- [x] 1.4 `streams/compat.py` — move `is_seekable`, `is_stream`, `is_filename`,
      `ensure_binaryio`, `ensure_bufferedio`, `fix_stream_start_position`,
      `read_exact`, plus `BinaryIOWrapper` (simplified — see section 2).
- [x] 1.5 Relocate decompressor streams: `decompressor_stream.py` →
      `streams/decompress.py`, `xz_stream.py` → `streams/xz.py`,
      `lzip_stream.py` → `streams/lzip.py`.
- [x] 1.6 Leave `archive_stream.py` where it is (clean and focused — no move).

## 2. Simplify BinaryIOWrapper

- [x] 2.1 Remove the method-replacement hot-path trick
      (`self.read = self._raw.read` after the first call).
- [x] 2.2 Replace with straightforward delegation, e.g.:
      ```python
      def read(self, size=-1):
          return self._raw.read(size)
      def readinto(self, b):
          if hasattr(self._raw, 'readinto'):
              return self._raw.readinto(b)
          data = self.read(len(b)); b[:len(data)] = data; return len(data)
      ```
- [x] 2.3 If a perf regression is suspected, benchmark a hot read loop
      before/after (noted as a risk in `PLAN.md`).

## 3. Update imports and keep a shim

- [x] 3.1 Repoint imports from `archivey.internal.io_helpers` to the new module
      paths across the codebase.
- [x] 3.2 Reduce `io_helpers.py` to a thin re-export shim so format backends are
      untouched this phase.

## 4. Verify — acceptance criteria

- [x] 4.1 `uv run pytest tests/` passes — identical results to Phase 1 (no
      behavior change).
- [x] 4.2 `uv run mypy src/` passes under `--strict`.
- [x] 4.3 `uv run ruff check` passes.
- [x] 4.4 `io_helpers.py` is ≤ 50 lines and contains only re-exports.
