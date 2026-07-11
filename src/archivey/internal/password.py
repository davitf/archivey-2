"""Internal password candidate resolution for encrypted archive units."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from collections.abc import Sequence as ABCSequence
from typing import TypeVar, cast

from archivey.config import PasswordInput, PasswordProvider, PasswordRequest
from archivey.exceptions import ArchiveyUsageError, EncryptionError
from archivey.types import ArchiveMember

_T = TypeVar("_T")


class _PasswordCandidatesExhausted(EncryptionError):
    """Internal marker: candidate decrypts failed or no candidate was supplied.

    An ``EncryptionError`` raised by the provider itself is deliberately not wrapped in
    this marker, so a backend can customize true candidate exhaustion without rewriting a
    provider-side failure.
    """

    def __init__(
        self, message: str, *, last_error: EncryptionError | None = None
    ) -> None:
        super().__init__(message)
        self.last_error = last_error


def _to_bytes(password: str | bytes) -> bytes:
    return password.encode() if isinstance(password, str) else password


class _PasswordCandidates:
    """Per-archive password state: known-good list, remaining candidates, optional provider.

    Under ``MemberStreams.CONCURRENT`` the known-good snapshot/promotion and provider
    callback are synchronized (D10): the provider is invoked with **no** Archivey lock
    held; same-reader provider reentry raises ``ArchiveyUsageError``. Concurrent
    first-touch may call the provider / attempt a candidate more than once — promotion
    still converges.
    """

    __slots__ = (
        "_candidates",
        "_known_good",
        "_provider",
        "_state_lock",
        "_provider_lock",
        "_provider_depth",
    )

    def __init__(
        self,
        *,
        candidates: ABCSequence[bytes] = (),
        provider: PasswordProvider | None = None,
    ) -> None:
        self._known_good: list[bytes] = []
        # Immutable static list: callers cannot mutate our candidate order after open.
        self._candidates: tuple[bytes, ...] = tuple(candidates)
        self._provider = provider
        self._state_lock = threading.Lock()
        self._provider_lock = threading.Lock()
        self._provider_depth = 0

    @classmethod
    def from_input(cls, password: PasswordInput) -> _PasswordCandidates:
        if password is None:
            return cls()
        if isinstance(password, (str, bytes)):
            return cls(candidates=[_to_bytes(password)])
        if isinstance(password, ABCSequence) and not isinstance(password, (str, bytes)):
            candidates_list: list[bytes] = []
            for item in password:
                if not isinstance(item, (str, bytes)):
                    raise TypeError(
                        "password sequence items must be str or bytes, "
                        f"not {type(item)!r}"
                    )
                candidates_list.append(_to_bytes(item))
            return cls(candidates=candidates_list)
        return cls(provider=cast(PasswordProvider, password))

    def has_passwords(self) -> bool:
        with self._state_lock:
            return bool(
                self._known_good or self._candidates or self._provider is not None
            )

    def is_ambiguous(self) -> bool:
        """Whether a weak password check needs confirmation before accepting a result.

        Duplicate static values count once. A provider is always potentially ambiguous:
        it is intentionally lazy and may return another value after a failed candidate,
        so a backend cannot soundly assume that its first answer is the only one.
        """
        with self._state_lock:
            distinct = set(self._known_good) | set(self._candidates)
            return len(distinct) > 1 or self._provider is not None

    def has_provider(self) -> bool:
        return self._provider is not None

    def ask_provider(self, member: ArchiveMember | None, attempt: int) -> bytes | None:
        """Return the provider's next answer, or ``None`` to stop.

        Invokes the provider with no Archivey lock held (the provider lock is released
        around the callback). Same-reader reentry raises ``ArchiveyUsageError``.
        """
        if self._provider is None:
            return None
        return self._call_provider(member, attempt)

    def _call_provider(
        self, member: ArchiveMember | None, attempt: int
    ) -> bytes | None:
        assert self._provider is not None
        with self._provider_lock:
            if self._provider_depth > 0:
                raise ArchiveyUsageError(
                    "Password provider reentered a password-requiring operation on the "
                    "same archive reader. Return a password (or None) without calling "
                    "back into archivey from the provider."
                )
            self._provider_depth += 1
        try:
            # Provider runs with no Archivey lock held (D10).
            raw = self._provider(PasswordRequest(member=member, attempt=attempt))
        finally:
            with self._provider_lock:
                self._provider_depth -= 1
        if raw is None:
            return None
        return _to_bytes(raw)

    def record_success(self, password: bytes) -> None:
        with self._state_lock:
            if password in self._known_good:
                self._known_good.remove(password)
            self._known_good.insert(0, password)

    def iter_candidates(self) -> Iterator[bytes]:
        """Yield passwords to try for one encrypted unit (known-good, then candidates)."""
        with self._state_lock:
            snapshot = (*self._known_good, *self._candidates)
        seen: set[bytes] = set()
        for password in snapshot:
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
        """Try passwords in order; consult the provider after static candidates fail.

        ``decrypt`` must return a non-``None`` value on success: ``attempt`` uses
        ``None`` as its "wrong password, try the next candidate" sentinel, so a
        decrypt callable that returned ``None`` for a valid password would be treated
        as a failure and retried. Decrypt / key derivation runs outside password-state
        locks; only promotion and provider reentry bookkeeping take the locks.
        """
        last_error: EncryptionError | None = None
        tried: set[bytes] = set()

        def try_password(password: bytes) -> _T | None:
            nonlocal last_error
            tried.add(password)
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

        for password in self.iter_candidates():
            result = try_password(password)
            if result is not None:
                return result

        attempt = 1
        while self._provider is not None:
            raw = self._call_provider(member, attempt)
            if raw is None:
                break
            password = raw
            # A provider that repeats a password we already tried can make no further
            # progress; stop rather than re-running an expensive decrypt (and, for 7z,
            # an expensive key derivation) on the same input forever.
            if password in tried:
                break
            result = try_password(password)
            if result is not None:
                return result
            attempt += 1

        message = (
            last_error.message
            if last_error is not None
            else "Password required to read this encrypted member"
        )
        exhausted = _PasswordCandidatesExhausted(message, last_error=last_error)
        if last_error is not None:
            raise exhausted from last_error
        raise exhausted
