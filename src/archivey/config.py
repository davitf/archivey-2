"""Public configuration types for archivey."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from archivey.diagnostics import DiagnosticPolicy, OnDiagnostic

if TYPE_CHECKING:
    from archivey.types import ArchiveMember


class AcceleratorMode(Enum):
    """Tri-state control for an optional random-access accelerator backend.

    - ``ON``  — always use the accelerator (raise ``PackageNotInstalledError`` if its
      package is absent: the caller asked for it explicitly).
    - ``OFF`` — never use it; the stream stays sequential-only.
    - ``AUTO`` — use it only when seekability was declared
      (``MemberStreams.SEEKABLE`` / seek demand). Without declared seek demand, AUTO
      leaves the cheaper sequential backend in place (no index/accelerator work). When
      AUTO would enable the accelerator but its package is absent, fall back to
      sequential silently (it is an enhancement, not a requirement).
    """

    AUTO = "auto"
    ON = "on"
    OFF = "off"

    def enabled_for(self, *, seekable: bool, available: bool) -> bool:
        """Resolve the tri-state to "use the accelerator?".

        ``ON`` always returns ``True`` (the caller checks availability and raises
        ``PackageNotInstalledError`` if the package is missing — the user asked for it
        explicitly). ``AUTO`` enables it only when seekability is declared and the
        package is available, so a missing package falls back silently.
        """
        if self is AcceleratorMode.OFF:
            return False
        if self is AcceleratorMode.ON:
            return True
        # AUTO: only pay for seek machinery when the caller asked for seekable streams.
        return available and seekable


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
    diagnostic_policy: DiagnosticPolicy = field(default_factory=DiagnosticPolicy)
    max_retained_diagnostic_references: int = 256
    on_diagnostic: OnDiagnostic | None = None


DEFAULT_ARCHIVEY_CONFIG = ArchiveyConfig()


@dataclass(frozen=True)
class PasswordRequest:
    """Context passed to a :data:`PasswordProvider` when a password is needed."""

    member: ArchiveMember | None
    """The member being decrypted, or ``None`` for archive-level (header) decryption."""

    attempt: int
    """1 on the first ask for this unit; increments after a wrong-password retry."""


PasswordProvider = Callable[[PasswordRequest], str | bytes | None]
"""Callable consulted when static password candidates fail for an encrypted unit."""

PasswordInput = str | bytes | Sequence[str | bytes] | PasswordProvider | None
