"""Internal configuration for the stream layer.

This is **not** part of the public API yet — ``open_archive()`` does not accept a
``config=`` argument in this phase. It exists so the codec/seekable layer can carry
the accelerator flags (and future stream options) as a single value instead of a
growing list of keyword arguments. A public surface is wired in a later phase (see
``access-mode-and-cost`` / the ``PLAN.md`` Phase-5 finalization).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum

# The random-access accelerators (rapidgzip, indexed_bzip2) can abort the process with SIGABRT
# at interpreter shutdown ("Detected Python finalization from running … thread"): they spawn
# C++ worker threads, and a thread still running at finalization trips their guard. archivey
# closes every accelerator stream via a weakref.finalize guard (see _AcceleratorStream in
# streams/codecs.py), which makes isolated leaked / cyclically-collected / never-closed streams
# shut down cleanly on *every* platform (proven by tests/test_accelerator_shutdown.py).
#
# However, the full test suite on macOS still aborts at shutdown once accelerators are active
# in-process, even with that guard — a residual we have not yet root-caused (the isolated canary
# does not reproduce it). Until it is understood, AUTO does not select an accelerator on macOS;
# gzip/bzip2 stay on the sequential stdlib backend there (a slow rewinding seek, warned about,
# beats crashing). An explicit ON is still honoured (the caller asked for it). See
# docs/known-issues.md and scripts/macos_accelerator_debug.py (a standalone reproduction to run
# on a real Mac to characterise the residual abort).
_ACCELERATORS_UNSAFE_PLATFORM = sys.platform == "darwin"


class AcceleratorMode(Enum):
    """Tri-state control for an optional random-access accelerator backend.

    - ``ON``  — always use the accelerator (raise ``PackageNotInstalledError`` if its
      package is absent: the caller asked for it explicitly).
    - ``OFF`` — never use it; the stream stays sequential-only.
    - ``AUTO`` — use it only when random access is actually wanted, i.e. the archive was
      opened for random access (``streaming=False``). Under ``streaming=True`` a forward
      pass needs no seeking, so AUTO leaves the cheaper sequential backend in place. When
      AUTO would enable the accelerator but its package is absent, fall back to sequential
      silently (it is an enhancement, not a requirement). On macOS, AUTO never selects an
      accelerator (the suite aborts at shutdown there — see ``_ACCELERATORS_UNSAFE_PLATFORM``).
    """

    AUTO = "auto"
    ON = "on"
    OFF = "off"

    def enabled_for(self, *, streaming: bool, available: bool) -> bool:
        """Resolve the tri-state to "use the accelerator?".

        ``ON`` always returns ``True`` (the caller checks availability and raises
        ``PackageNotInstalledError`` if the package is missing — the user asked for it
        explicitly). ``AUTO`` enables it only for random access and only when available, so
        a missing package falls back silently to the sequential backend.
        """
        if self is AcceleratorMode.OFF:
            return False
        if self is AcceleratorMode.ON:
            return True
        # AUTO: random access wants seeking; a forward-only pass does not. On macOS the suite
        # still aborts at shutdown with accelerators active, so AUTO does not select them there.
        return available and not streaming and not _ACCELERATORS_UNSAFE_PLATFORM


@dataclass(frozen=True)
class StreamConfig:
    """Options that influence how compressed streams are opened.

    ``streaming`` mirrors the archive's access mode (``open_archive(streaming=...)``) so
    the accelerator modes can resolve ``AUTO`` against it.
    """

    streaming: bool = False
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO


# A shared default used by callers that have no specific configuration (e.g. opening a
# bare single-file compressed stream in random-access mode).
DEFAULT_STREAM_CONFIG = StreamConfig()
