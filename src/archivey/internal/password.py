"""Internal password candidate resolution for encrypted archive units."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence as ABCSequence
from typing import TypeVar

from archivey.config import PasswordInput, PasswordProvider, PasswordRequest
from archivey.exceptions import EncryptionError
from archivey.types import ArchiveMember

_T = TypeVar("_T")


def _to_bytes(password: str | bytes) -> bytes:
    return password.encode() if isinstance(password, str) else password


class _PasswordCandidates:
    """Per-archive password state: known-good list, remaining candidates, optional provider."""

    __slots__ = ("_candidates", "_known_good", "_provider")

    def __init__(
        self,
        *,
        candidates: ABCSequence[bytes] = (),
        provider: PasswordProvider | None = None,
    ) -> None:
        self._known_good: list[bytes] = []
        self._candidates: list[bytes] = list(candidates)
        self._provider = provider

    @classmethod
    def from_input(cls, password: PasswordInput) -> _PasswordCandidates:
        if password is None:
            return cls()
        if isinstance(password, (str, bytes)):
            return cls(candidates=[_to_bytes(password)])
        if isinstance(password, ABCSequence):
            return cls(candidates=[_to_bytes(p) for p in password])
        if callable(password):
            return cls(provider=password)
        raise TypeError(f"unsupported password type: {type(password)!r}")

    def has_passwords(self) -> bool:
        return bool(self._known_good or self._candidates or self._provider is not None)

    def record_success(self, password: bytes) -> None:
        if password in self._known_good:
            self._known_good.remove(password)
        self._known_good.insert(0, password)

    def iter_candidates(self, member: ArchiveMember | None) -> Iterator[bytes]:
        """Yield passwords to try for one encrypted unit (known-good, then candidates)."""
        seen: set[bytes] = set()
        for password in self._known_good:
            if password not in seen:
                seen.add(password)
                yield password
        for password in self._candidates:
            if password not in seen:
                seen.add(password)
                yield password

    def attempt(
        self,
        member: ArchiveMember | None,
        decrypt: Callable[[bytes], _T],
        *,
        on_failure: Callable[[bytes, Exception], EncryptionError | None] | None = None,
    ) -> _T:
        """Try passwords in order; consult the provider after static candidates fail."""
        last_error: EncryptionError | None = None

        def try_password(password: bytes) -> _T | None:
            nonlocal last_error
            try:
                result = decrypt(password)
            except EncryptionError as exc:
                last_error = exc
                if on_failure is not None:
                    mapped = on_failure(password, exc)
                    if mapped is not None:
                        last_error = mapped
                return None
            self.record_success(password)
            return result

        for password in self.iter_candidates(member):
            result = try_password(password)
            if result is not None:
                return result

        attempt = 1
        while self._provider is not None:
            raw = self._provider(PasswordRequest(member=member, attempt=attempt))
            if raw is None:
                break
            result = try_password(_to_bytes(raw))
            if result is not None:
                return result
            attempt += 1

        if last_error is not None:
            raise last_error
        raise EncryptionError("Password required to read this encrypted member")
