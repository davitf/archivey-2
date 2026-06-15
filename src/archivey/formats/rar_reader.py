import collections
import datetime
import enum
import functools
import hashlib
import hmac
import io
import logging
import os
import shutil
import stat
import struct
import subprocess
import tempfile
import threading
import zlib
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Optional,
    cast,
)

from archivey.config import ExtractionFilter

if TYPE_CHECKING:
    import rarfile
    from rarfile import Rar3Info, Rar5Info, RarInfo
else:
    try:
        import rarfile
        from rarfile import Rar3Info, Rar5Info, RarInfo
    except ImportError:
        rarfile = None  # type: ignore[assignment]
        Rar3Info = object  # type: ignore[assignment]
        Rar5Info = object  # type: ignore[assignment]
        RarInfo = object  # type: ignore[assignment]

from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEncryptedError,
    ArchiveError,
    ArchiveStreamNotSeekableError,
    PackageNotInstalledError,
)
from archivey.internal.base_reader import BaseArchiveReader, _build_filter
from archivey.internal.io_helpers import (
    ErrorIOStream,
    ensure_binaryio,
    is_seekable,
    is_stream,
    run_with_exception_translation,
)
from archivey.internal.utils import (
    bytes_to_str,
    ensure_not_none,
    str_to_bytes,
)
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CreateSystem,
    IteratorFilterFunc,
    MemberType,
)

logger = logging.getLogger(__name__)


def get_non_corrupted_filename(rarinfo: RarInfo) -> str | None:
    """
    Returns the most accurate filename for a RAR entry, working around a known issue
    in RAR 2.9–4 where the UTF-16 filename field may truncate characters outside the
    Basic Multilingual Plane (e.g. emoji) to invalid code units like U+F600 or surrogates.

    This function compares the legacy 8-bit filename (`orig_filename`, often UTF-8) and
    the decoded UTF-16 version (`filename`) character by character. If it finds that the
    UTF-16 version contains a suspicious character (PUA or surrogate) in a position where
    the UTF-8 version contains a valid non-BMP character, it prefers the UTF-8 version.

    Issue found and fixed in collaboration with ChatGPT.

    Returns:
        The corrected filename string, or None if decoding fails.
    """
    if not isinstance(rarinfo, Rar3Info):
        return cast("str | None", rarinfo.filename)
    if not rarinfo.flags & rarfile.RAR_FILE_UNICODE:
        return cast("str | None", rarinfo.filename)

    utf16_str = cast("str | None", rarinfo.filename)

    try:
        utf8_str = cast("bytes", rarinfo.orig_filename).decode("utf-8")
    except UnicodeDecodeError:
        return utf16_str

    if utf16_str is None:
        return utf8_str

    if utf16_str == utf8_str:
        return utf8_str  # Equal strings, no correction needed

    # Compare character by character to detect corrupted characters
    for i, (u16c, u8c) in enumerate(zip(utf16_str, utf8_str)):
        u16_code = ord(u16c)
        u8_code = ord(u8c)

        # Check for likely corruption:
        # - UTF-16 char is in the surrogate or PUA range
        # - UTF-8 char is a valid non-BMP character (requires surrogate pair in UTF-16)
        if (
            0xD800 <= u16_code <= 0xDFFF or 0xE000 <= u16_code <= 0xF8FF
        ) and u8_code == u16_code + 0x10000:
            logger.warning(
                f"UTF-16 filename appears truncated at char {i}: "
                f"{utf16_str!r} vs {utf8_str!r} – preferring UTF-8"
            )
            return utf8_str

    # If we got here, no strong evidence of corruption — trust the UTF-16 version
    return utf16_str


_RAR_COMPRESSION_METHODS = {
    0x30: "store",
    0x31: "fastest",
    0x32: "fast",
    0x33: "normal",
    0x34: "good",
    0x35: "best",
}

_RAR_HOST_OS_TO_CREATE_SYSTEM = {
    0: CreateSystem.FAT,
    1: CreateSystem.OS2_HPFS,
    2: CreateSystem.NTFS,
    3: CreateSystem.UNIX,
    4: CreateSystem.MACINTOSH,
    5: CreateSystem.UNKNOWN,  # BeOS is not represented
}

RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS = 0x2
RAR_ENCDATA_FLAG_HAS_PASSWORD_CHECK_DATA = 0x1


RarEncryptionInfo = collections.namedtuple(
    "RarEncryptionInfo", ["algo", "flags", "kdf_count", "salt", "iv", "check_value"]
)


def is_rar_info_hardlink(rarinfo: RarInfo) -> bool:
    if not isinstance(rarinfo, Rar5Info):
        return False
    return (
        rarinfo.file_redir is not None
        and rarinfo.file_redir[0] == rarfile.RAR5_XREDIR_HARD_LINK
    )


def get_encryption_info(rarinfo: RarInfo) -> RarEncryptionInfo | None:
    # The file_encryption attribute is not publicly defined, but it's there.
    if not isinstance(rarinfo, Rar5Info):
        return None
    if rarinfo.file_encryption is None:
        return None
    return RarEncryptionInfo(*rarinfo.file_encryption)


class PasswordCheckResult(enum.Enum):
    CORRECT = 1
    INCORRECT = 2
    UNKNOWN = 3


@functools.lru_cache(maxsize=128)
def _verify_rar5_password_internal(
    password: bytes, salt: bytes, kdf_count: int, check_value: bytes
) -> PasswordCheckResult:
    # Mostly copied from RAR5Parser._check_password
    RAR5_PW_CHECK_SIZE = 8
    RAR5_PW_SUM_SIZE = 4

    if len(check_value) != RAR5_PW_CHECK_SIZE + RAR5_PW_SUM_SIZE:
        return PasswordCheckResult.UNKNOWN  # Unnown algorithm

    hdr_check = check_value[:RAR5_PW_CHECK_SIZE]
    hdr_sum = check_value[RAR5_PW_CHECK_SIZE:]
    sum_hash = hashlib.sha256(hdr_check).digest()
    if sum_hash[:RAR5_PW_SUM_SIZE] != hdr_sum:
        # Unknown algorithm?
        return PasswordCheckResult.UNKNOWN

    iterations = (1 << kdf_count) + 32
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)

    pwd_check = bytearray(RAR5_PW_CHECK_SIZE)
    len_mask = RAR5_PW_CHECK_SIZE - 1
    for i, v in enumerate(pwd_hash):
        pwd_check[i & len_mask] ^= v

    if pwd_check != hdr_check:
        return PasswordCheckResult.INCORRECT

    return PasswordCheckResult.CORRECT


def verify_rar5_password(
    password: bytes | None, rar_info: RarInfo
) -> PasswordCheckResult:
    """
    Verifies whether the given password matches the check value in RAR5 encryption data.
    Returns True if the password is correct, False if not.
    """
    if not rar_info.needs_password():
        return PasswordCheckResult.CORRECT
    if password is None:
        return PasswordCheckResult.INCORRECT
    encdata = get_encryption_info(rar_info)
    if not encdata or not encdata.flags & RAR_ENCDATA_FLAG_HAS_PASSWORD_CHECK_DATA:
        return PasswordCheckResult.UNKNOWN

    return _verify_rar5_password_internal(
        password, encdata.salt, encdata.kdf_count, encdata.check_value
    )


@functools.lru_cache(maxsize=128)
def _rar_hash_key(password: bytes, salt: bytes, kdf_count: int) -> bytes:
    iterations = 1 << kdf_count
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations + 16)


def convert_crc_to_encrypted(
    crc: int, password: bytes, salt: bytes, kdf_count: int
) -> int:
    """Convert a CRC32 to the encrypted format used in RAR5 archives.

    This implements the ConvertHashToMAC function from the RAR source code.
    First creates a hash key using PBKDF2 with the password and salt,
    then uses that key for HMAC-SHA256 of the CRC.
    """
    # Convert password to UTF-8 if it isn't already
    if isinstance(password, str):
        password = password.encode("utf-8")

    hash_key = _rar_hash_key(password, salt, kdf_count)

    # Convert CRC to bytes
    raw_crc = crc.to_bytes(4, "little")

    # Compute HMAC-SHA256 of the CRC using the hash key
    digest = hmac.new(hash_key, raw_crc, hashlib.sha256).digest()

    # XOR the digest bytes into the CRC
    result = 0
    for i in struct.iter_unpack("<I", digest):
        result ^= i[0]

    return result


def check_rarinfo_crc(
    rarinfo: RarInfo, password: bytes | None, computed_crc: int
) -> bool:
    encryption_info = get_encryption_info(rarinfo)
    if (
        not encryption_info
        or not encryption_info.flags & RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS
    ):
        return bool(computed_crc == rarinfo.CRC)

    if password is None:
        logger.warning("No password specified for checking %s", rarinfo.filename)
        return False

    converted = convert_crc_to_encrypted(
        computed_crc, password, encryption_info.salt, encryption_info.kdf_count
    )
    return bool(converted == rarinfo.CRC)


class RarStreamMemberFile(io.RawIOBase, BinaryIO):
    def __init__(
        self,
        member: ArchiveMember,
        shared_stream: IO[bytes],
        lock: threading.Lock,
        *,
        pwd: bytes | None = None,
    ):
        super().__init__()
        self._member_pwd = pwd  # Store the password
        self._stream = shared_stream
        assert member.file_size is not None
        self._remaining: int = member.file_size
        self._expected_crc = (
            member.crc32 & 0xFFFFFFFF if member.crc32 is not None else None
        )
        self._expected_encrypted_crc: int | None = (
            member.extra.get("encrypted_crc", None) if member.extra else None
        )
        self._actual_crc = 0
        self._lock = lock
        self._filename = member.filename
        self._fully_read = False
        self._member = member
        self._crc_checked = False

    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError(f"Cannot read from closed/expired file: {self._filename}")

        with self._lock:
            if self._remaining == 0:
                self._fully_read = True
                self._check_crc()
                return b""

            to_read = self._remaining if n < 0 else min(self._remaining, n)
            data = self._stream.read(to_read)
            if not data:
                raise EOFError(f"Unexpected EOF while reading {self._filename}")
            self._remaining -= len(data)
            self._actual_crc = zlib.crc32(data, self._actual_crc)

            if self._remaining == 0:
                self._fully_read = True
                self._check_crc()

            return data

    def _check_crc(self) -> None:
        if self._crc_checked:
            return
        self._crc_checked = True

        matches = check_rarinfo_crc(
            cast("RarInfo", self._member.raw_info), self._member_pwd, self._actual_crc
        )
        if not matches:
            raise ArchiveCorruptedError(f"CRC mismatch in {self._filename}")

    def readable(self) -> bool:
        return True  # pragma: no cover

    def writable(self) -> bool:
        return False  # pragma: no cover

    def seekable(self) -> bool:
        return False  # pragma: no cover

    def write(self, b: Any) -> int:
        raise io.UnsupportedOperation("write")  # pragma: no cover

    def writelines(self, lines: Iterable[Any]) -> None:
        raise io.UnsupportedOperation("writelines")  # pragma: no cover

    def close(self) -> None:
        if self.closed:
            return
        try:
            with self._lock:
                while self._remaining > 0:
                    chunk = self.read(min(65536, self._remaining))
                    if not chunk:
                        raise EOFError(
                            f"Unexpected EOF while skipping {self._filename}"
                        )

            self._check_crc()
        finally:
            super().close()


# Streams archive data once by running ``unrar p`` in a subprocess.
# ``rarfile`` only extracts one file at a time, which in a solid archive would
# require re-decompressing all earlier members for each ``open()`` call. By
# invoking the external ``unrar p`` command we decompress sequentially a single
# time and yield the file streams one after another.
class RarStreamReader:
    def __init__(
        self,
        archive_path: BinaryIO | str,
        members: list[ArchiveMember],
        *,
        pwd: bytes | str | None = None,
    ):
        self._pwd = bytes_to_str(pwd)
        self.archive_path = archive_path
        self._open_unrar_stream()
        self._lock = threading.Lock()
        self._members = members

    def _open_unrar_stream(self) -> None:
        try:
            unrar_path = shutil.which("unrar")
            if not unrar_path:
                raise PackageNotInstalledError(
                    "unrar command is not installed. It is required to read RAR member contents."
                )

            # Open an unrar process that outputs the contents of all files in the archive to stdout.
            password_args = ["-p" + bytes_to_str(self._pwd)] if self._pwd else ["-p-"]
            cmd: list[str] = [
                unrar_path,
                "p",
                "-inul",
                *password_args,
                cast("str", self.archive_path),
            ]
            logger.debug(
                "Opening RAR archive %s with command: %s",
                self.archive_path,
                " ".join(cmd),
            )
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                bufsize=1024 * 1024,
            )
            if self._proc.stdout is None:
                raise RuntimeError("Could not open unrar output stream")
            self._stream = self._proc.stdout

        except (OSError, subprocess.SubprocessError) as e:
            raise ArchiveError(
                f"Error opening RAR archive {self.archive_path}: {e}"
            ) from e

    def _get_member_file(self, member: ArchiveMember) -> BinaryIO | None:
        if not member.is_file:
            return None

        pwd_bytes = str_to_bytes(self._pwd) if self._pwd is not None else None
        if (
            member.encrypted
            and verify_rar5_password(pwd_bytes, cast("RarInfo", member.raw_info))
            == PasswordCheckResult.INCORRECT
        ):
            # unrar silently skips encrypted files with incorrect passwords
            return ErrorIOStream(
                ArchiveEncryptedError(f"Wrong password specified for {member.filename}")
            )

        return RarStreamMemberFile(member, self._stream, self._lock, pwd=pwd_bytes)

    def close(self) -> None:
        self._proc.terminate()
        self._proc.wait()
        self._stream.close()

    def rar_stream_iterator(self) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        """Reader for RAR archives using the solid stream reader.

        This may fail for non-solid archives where some files are encrypted and others not,
        or there are multiple passwords. If the password is incorrect for some files,
        they will be silently skipped, so the successfully output data will be associated
        with the wrong files. (ideally, use this only for solid archives, which are
        guaranteed to have the same password for all files)
        """

        logger.debug("Iterating over %s members", len(self._members))

        members = sorted(self._members, key=lambda m: m.member_id)

        try:
            for member in members:
                stream = self._get_member_file(member)
                yield member, stream
                if stream is not None:
                    # If the caller hasn't read the stream, close() will read any
                    # remaining data.
                    stream.close()
        finally:
            self.close()


class RarReader(BaseArchiveReader):
    """Base class for RAR archive readers."""

    def __init__(
        self,
        format: ArchiveFormat,
        archive_path: str | BinaryIO | os.PathLike[str],
        *,
        pwd: bytes | str | None = None,
        streaming_only: bool = False,
    ):
        if format != ArchiveFormat.RAR:
            raise ValueError(f"Unsupported archive format: {format}")

        super().__init__(
            format=format,
            archive_path=archive_path,
            streaming_only=streaming_only,
            members_list_supported=True,
            pwd=pwd,
        )

        if is_stream(self.path_or_stream) and not is_seekable(self.path_or_stream):
            raise ArchiveStreamNotSeekableError(
                "RAR archives do not support non-seekable streams"
            )

        self._format_info: Optional[ArchiveInfo] = None

        if rarfile is None:
            raise PackageNotInstalledError(
                "rarfile package is not installed. Please install it to work with RAR archives."
            )

        def open_rar_file() -> Any:
            r = rarfile.RarFile(archive_path, "r", pwd, errors="strict")
            if pwd:
                r.setpassword(pwd)
            return r

        self._archive = run_with_exception_translation(
            open_rar_file,
            self._translate_exception,
            archive_path=str(archive_path),
        )

    def _close_archive(self) -> None:
        """Close the archive and release any resources."""
        self._archive.close()
        self._archive = None

    def _get_link_target(
        self, info: RarInfo, *, pwd: bytes | str | None = None
    ) -> Optional[str]:
        """Return the link target for ``info`` if available.

        If the link target is encrypted and ``pwd`` is not provided or is
        incorrect, a warning is logged and ``None`` is returned.
        """
        assert self._archive is not None

        if not info.is_symlink() and not is_rar_info_hardlink(info):
            return None

        if info.file_redir:
            return cast("str | None", info.file_redir[2])

        try:
            data = self._archive.read(info.filename, pwd=bytes_to_str(pwd))
        except rarfile.PasswordRequired:
            logger.warning(
                "Password required to read link target for %s", info.filename
            )
            return None
        except rarfile.RarWrongPassword:
            logger.warning("Wrong password specified for link target %s", info.filename)
            return None
        except rarfile.Error as e:
            logger.warning("Error reading link target for %s: %s", info.filename, e)
            data = b""

        if not data:
            if is_stream(self.path_or_stream):
                logger.warning(
                    "Cannot read link targets for RAR4 file when opening from a stream"
                )
                return None

            unrar = shutil.which("unrar")
            if unrar:
                try:
                    assert info.filename is not None
                    with tempfile.TemporaryDirectory() as tmpdir:
                        subprocess.run(
                            [
                                unrar,
                                "x",
                                "-inul",
                                f"-p{bytes_to_str(pwd) if pwd is not None else '-'}",
                                str(self.path_str),
                                info.filename,
                                tmpdir,
                            ],
                            check=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        link_path = os.path.join(tmpdir, info.filename)
                        return os.readlink(link_path)
                except (subprocess.SubprocessError, OSError) as e:
                    logger.warning(
                        "Error reading link target via unrar for %s: %s",
                        info.filename,
                        e,
                    )
            return None

        return cast("str", data.decode("utf-8"))

    def _get_timestamp(self, info: RarInfo) -> datetime.datetime | None:
        if info.mtime is not None:
            # RAR5 stores timestamps in UTC, but RAR4 does not. rarfile already returns
            # mtimes with and without the timezone set correctly.
            return cast("datetime.datetime | None", info.mtime)
        if info.date_time is not None:
            # For some reason (bug in rarfile?), directories in RAR4 archives have no mtime,
            # but they do have a date_time.
            return cast("datetime.datetime | None", rarfile.to_datetime(info.date_time))
        return None

    def iter_members_for_registration(self) -> Iterator[ArchiveMember]:
        assert self._archive is not None

        rarinfos: list[RarInfo] = self._archive.infolist()
        for info in rarinfos:
            compression_method = (
                _RAR_COMPRESSION_METHODS.get(info.compress_type, "unknown")
                if info.compress_type is not None
                else None
            )

            # According to https://documentation.help/WinRAR/HELPArcEncryption.htm :
            # If "Encrypt file names" [i.e. header encryption] option is off,
            # file checksums for encrypted RAR 5.0 files are modified using a
            # special password dependent algorithm. [...] So do not expect checksums
            # for encrypted RAR 5.0 files to match actual CRC32 or BLAKE2 values.
            # If "Encrypt file names" option is on, checksums are stored without modification,
            # because they can be accessed only after providing a valid password.

            encryption_info = get_encryption_info(info)
            if encryption_info:
                has_encrypted_crc = bool(
                    encryption_info.flags & RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS
                )
            else:
                has_encrypted_crc = False

            corrected_filename = get_non_corrupted_filename(info) or ""
            member = ArchiveMember(
                filename=corrected_filename,
                raw_filename=info.filename or None,
                file_size=info.file_size,
                compress_size=info.compress_size,
                mtime_with_tz=self._get_timestamp(info),
                type=(
                    MemberType.HARDLINK
                    if is_rar_info_hardlink(info)
                    else MemberType.DIR
                    if info.is_dir()
                    else MemberType.FILE
                    if info.is_file()
                    else MemberType.SYMLINK
                    if info.is_symlink()
                    else MemberType.OTHER
                ),
                mode=stat.S_IMODE(info.mode)
                if hasattr(info, "mode") and isinstance(info.mode, int)
                else None,
                crc32=info.CRC if not has_encrypted_crc else None,
                compression_method=compression_method,
                comment=info.comment,
                encrypted=info.needs_password(),
                create_system=_RAR_HOST_OS_TO_CREATE_SYSTEM.get(
                    info.host_os, CreateSystem.UNKNOWN
                )
                if info.host_os is not None
                else None,
                windows_attrs=info.mode
                if hasattr(info, "mode")
                and isinstance(info.mode, int)
                and getattr(info, "host_os", None) == rarfile.RAR_OS_WIN32
                else None,
                raw_info=info,
                link_target=self._get_link_target(info),
                extra={"host_os": getattr(info, "host_os", None)},
            )
            yield member

        logger.debug("iter_members_for_registration: done")

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format.

        Returns:
            ArchiveInfo: Detailed format information
        """
        self.check_archive_open()
        assert self._archive is not None

        if self._format_info is None:
            version = rarfile.get_rar_version(self.path_or_stream)
            version_str = str(version) if version else "unknown"

            has_header_encryption = (
                self._archive._file_parser is not None
                and self._archive._file_parser.has_header_encryption()
            )

            self._format_info = ArchiveInfo(
                format=self.format,
                version=version_str,
                is_solid=getattr(
                    self._archive, "is_solid", lambda: False
                )(),  # rarfile < 4.1 doesn't have is_solid
                comment=self._archive.comment,
                extra={
                    "needs_password": self._archive.needs_password(),
                    "header_encrypted": has_header_encryption,
                },
            )

        return self._format_info

    def _translate_exception(self, e: Exception) -> Optional[ArchiveError]:
        if isinstance(e, rarfile.BadRarFile):
            return ArchiveCorruptedError("Error reading RAR archive")
        if isinstance(e, rarfile.RarWrongPassword):
            return ArchiveEncryptedError("Wrong password specified")
        if isinstance(e, rarfile.PasswordRequired):
            return ArchiveEncryptedError("Password required")
        if isinstance(e, rarfile.NotRarFile):
            return ArchiveCorruptedError("Not a RAR archive")
        if isinstance(e, rarfile.NeedFirstVolume):
            return ArchiveError("Need first volume of multi-volume RAR archive")
        if isinstance(e, rarfile.NoCrypto):
            return PackageNotInstalledError("cryptography package is not installed")
        if isinstance(e, rarfile.Error):
            return ArchiveError("Unknown error reading RAR archive")
        if isinstance(e, io.UnsupportedOperation) and (
            "seek" in str(e) or "non buffered" in str(e)
        ):
            return ArchiveStreamNotSeekableError(
                "RAR archives do not support non-seekable streams"
            )
        return None

    def _prepare_member_for_open(
        self, member: ArchiveMember, *, pwd: bytes | str | None, for_iteration: bool
    ) -> ArchiveMember:
        if pwd is not None and member.is_link and member.link_target is None:
            link_target = self._get_link_target(
                cast("RarInfo", member.raw_info),
                pwd=pwd,
            )
            if link_target is not None:
                member.link_target = link_target
            else:
                raise ArchiveEncryptedError(
                    f"Cannot read link target for {member.filename}"
                )
        return member

    def _open_member(
        self,
        member: ArchiveMember,
        pwd: str | bytes | None,
        for_iteration: bool,
    ) -> BinaryIO:
        assert member.type == MemberType.FILE

        if member.encrypted:
            password = str_to_bytes(
                pwd if pwd is not None else self.get_archive_password()
            )
            if password is None:
                raise ArchiveEncryptedError(
                    "Password required",
                    archive_path=self.path_str,
                    member_name=member.filename,
                )
            pwd_check = verify_rar5_password(
                password,
                cast("RarInfo", member.raw_info),
            )
            if pwd_check == PasswordCheckResult.INCORRECT:
                raise ArchiveEncryptedError(
                    "Wrong password specified",
                    archive_path=self.path_str,
                    member_name=member.filename,
                )

        return ensure_binaryio(
            cast(
                "IO[bytes]",
                ensure_not_none(self._archive).open(
                    member.raw_info, pwd=bytes_to_str(pwd)
                ),
            )
        )

    def iter_members_with_streams(
        self,
        members: Collection[ArchiveMember | str]
        | Callable[[ArchiveMember], bool]
        | None = None,
        *,
        pwd: bytes | str | None = None,
        filter: IteratorFilterFunc | ExtractionFilter | None = None,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        if self.config.use_rar_stream:
            logger.debug("iter_members_with_streams: using rar_stream_reader")
            pwd_to_use = pwd if pwd is not None else self.get_archive_password()

            # This never returns None for archives with member list support.
            members = self.get_members_if_available()
            assert members is not None

            if self.path_str is None:
                raise ValueError("RAR stream reader cannot be opened from a stream")

            stream_reader = RarStreamReader(self.path_str, members, pwd=pwd_to_use)
            filter_func = _build_filter(
                members, filter or self.config.extraction_filter, None
            )
            for member, stream in stream_reader.rar_stream_iterator():
                filtered_member = filter_func(member)
                if filtered_member is None:
                    continue
                yield filtered_member, stream

        else:
            logger.debug("iter_members_with_streams: not using rar_stream_reader")
            yield from super().iter_members_with_streams(
                members,
                pwd=pwd,
                filter=filter,
            )

    @classmethod
    def is_rar_file(cls, file: BinaryIO | str | os.PathLike[str]) -> bool:
        if rarfile is not None:
            return bool(rarfile.is_rarfile(file) or rarfile.is_rarfile_sfx(file))

        return False
