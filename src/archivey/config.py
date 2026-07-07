"""Public configuration types for archivey."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class AcceleratorMode(Enum):
    """Tri-state control for an optional random-access accelerator backend.

    - ``ON``  — always use the accelerator (raise ``PackageNotInstalledError`` if its
      package is absent: the caller asked for it explicitly).
    - ``OFF`` — never use it; the stream stays sequential-only.
    - ``AUTO`` — use it only when random access is actually wanted, i.e. the archive was
      opened for random access (``streaming=False``). Under ``streaming=True`` a forward
      pass needs no seeking, so AUTO leaves the cheaper sequential backend in place. When
      AUTO would enable the accelerator but its package is absent, fall back to sequential
      silently (it is an enhancement, not a requirement).
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
        # AUTO: random access wants seeking; a forward-only pass does not.
        return available and not streaming


@dataclass(frozen=True)
class ExtractionLimits:
    """Decompression-bomb limits for :func:`archivey.extract` / :meth:`extract_all`.

    ``None`` on a guard field disables that guard. :attr:`UNLIMITED` disables all four.
    """

    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar[ExtractionLimits]


ExtractionLimits.UNLIMITED = ExtractionLimits(
    max_extracted_bytes=None,
    max_ratio=None,
    max_entries=None,
)


@dataclass(frozen=True)
class ArchiveyConfig:
    """Library tuning knobs passed explicitly as ``config=`` to :func:`open_archive` / :func:`extract`.

    Per-call operationals (``format``, ``streaming``, ``password``, extraction's
    ``members``/``filter``/``policy``/…) stay keyword arguments — not part of this object.
    """

    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()


DEFAULT_ARCHIVEY_CONFIG = ArchiveyConfig()
