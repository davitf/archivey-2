import collections
import io
import logging
import lzma
import os
import pathlib
import struct
from abc import abstractmethod
from queue import Empty, Queue
from threading import Lock, Thread
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Callable,
    Collection,
    Iterator,
    Optional,
    Union,
    cast,
)

from archivey.config import ExtractionFilter
from archivey.internal.base_reader import (
    BaseArchiveReader,
    _build_filter,
    _build_member_included_func,
)
from archivey.internal.io_helpers import (
    ErrorIOStream,
    is_seekable,
    is_stream,
    run_with_exception_translation,
)

if TYPE_CHECKING:
    import py7zr
    import py7zr.compressor
    import py7zr.exceptions
    import py7zr.helpers
    import py7zr.io
    from py7zr import Py7zIO, WriterFactory
    from py7zr.py7zr import ArchiveFile
else:
    try:
        import py7zr
        import py7zr.compressor
        import py7zr.exceptions
        import py7zr.helpers
        import py7zr.io
        from py7zr import Py7zIO, WriterFactory
        from py7zr.py7zr import ArchiveFile
    except ImportError:
        py7zr = None  # type: ignore[assignment]
        ArchiveFile = None  # type: ignore[misc,assignment]
        Py7zIO = object  # type: ignore[misc,assignment]
        WriterFactory = object  # type: ignore[misc,assignment]


from contextlib import contextmanager

from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEncryptedError,
    ArchiveEOFError,
    ArchiveError,
    ArchiveStreamNotSeekableError,
    ArchiveUnsupportedFeatureError,
    PackageNotInstalledError,
)
from archivey.internal.extraction_helper import ExtractionHelper
from archivey.internal.utils import bytes_to_str
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    IteratorFilterFunc,
    MemberType,
)

logger = logging.getLogger(__name__)


class BasePy7zIOWriter(Py7zIO):
    def seek(self, offset: int, whence: int = 0) -> int:
        if offset == 0 and whence == 0:
            self.close()
            return 0
        raise io.UnsupportedOperation()

    @abstractmethod
    def close(self) -> None:
        pass

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def read(self, size: Optional[int] = None) -> bytes:
        raise io.UnsupportedOperation()

    def flush(self) -> None:
        raise io.UnsupportedOperation()

    def size(self) -> int:
        raise io.UnsupportedOperation()


class StreamingFile(BasePy7zIOWriter):
    class Reader(io.RawIOBase, BinaryIO):
        def __init__(self, parent: "StreamingFile", pwd: bytes | str | None = None):
            self._parent = parent
            self._buffer = bytearray()
            self._eof = False
            self._first_read = True

        def read(self, size: int = -1) -> bytes:
            if self.closed:
                raise ValueError("Stream is closed")

            self._first_read = False
            while not self._eof and (size < 0 or len(self._buffer) < size):
                try:
                    chunk = self._parent._data_queue.get(timeout=0.1)
                    if chunk is None:
                        self._eof = True
                        break
                    self._buffer.extend(chunk)
                except Empty:
                    continue

            if size < 0:
                size = len(self._buffer)

            data = self._buffer[:size]
            self._buffer = self._buffer[size:]
            return bytes(data)

        def readinto(self, buffer: bytearray) -> int:  # type: ignore[override]
            data = self.read(len(buffer))
            buffer[: len(data)] = data
            return len(data)

        def close(self) -> None:
            self._parent._reader_alive = False
            self._parent._data_queue.put(None)
            super().close()
            self._buffer = bytearray()
            self._eof = True

        def readable(self) -> bool:
            return True

        def writable(self) -> bool:
            return False

        def seekable(self) -> bool:
            return False

        def __enter__(self) -> "StreamingFile.Reader":
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            self.close()

        # TODO: do we need to implement readall / readinto?

    def __init__(
        self,
        fname: str,
        files_queue: "Queue[tuple[str, BinaryIO] | None]",
        max_chunks: int = 64,
        pwd: bytes | str | None = None,
    ):
        self._fname = fname
        self._data_queue: "Queue[bytes | bytearray | None]" = Queue(maxsize=max_chunks)
        self._reader_alive = True
        self._files_queue = files_queue
        self._reader = self.Reader(self)
        self._started = False
        self._closed = False

    def write(self, s: Union[bytes, bytearray]) -> int:
        if not self._started:
            self._started = True
            self._files_queue.put((self._fname, self._reader))
        if not self._reader_alive:
            return 0
        self._data_queue.put(s)
        return len(s)

    def close(self):
        if not self._closed:
            self._data_queue.put(None)
            self._closed = True


class StreamingFactory(WriterFactory):
    def __init__(self, q: Queue, pwd: bytes | str | None = None):
        self._queue = q

    def create(self, filename: str) -> Py7zIO:
        return StreamingFile(filename, self._queue)

    def yield_files(self) -> Iterator[tuple[str, BinaryIO]]:
        while True:
            item = self._queue.get()
            if item is None:
                break
            yield item

    def finish(self):
        self._queue.put(None)


class ExtractFileWriter(BasePy7zIOWriter):
    def __init__(self, full_path: str, pwd: bytes | str | None = None):
        self.full_path = full_path
        os.makedirs(os.path.dirname(self.full_path), exist_ok=True)

        self.file = open(self.full_path, "wb")

    def write(self, s: Union[bytes, bytearray]) -> int:
        self.file.write(s)
        return len(s)

    def close(self):
        logger.debug("Closing file writer for %s", self.full_path)
        self.file.close()


class ExtractLinkWriter(BasePy7zIOWriter):
    def __init__(self, member: ArchiveMember, pwd: bytes | str | None = None):
        self.data = bytearray()
        self.member = member

    def write(self, s: Union[bytes, bytearray]) -> int:
        self.data.extend(s)
        return len(s)

    def close(self):
        self.member.link_target = self.data.decode("utf-8")


class ExtractWriterFactory(WriterFactory):
    def __init__(
        self,
        path: str,
        extract_filename_to_member: dict[str, ArchiveMember],
    ):
        self._path = path
        self._extract_filename_to_member = extract_filename_to_member
        self.member_id_to_outfile: dict[int, str] = {}
        self.outfiles: set[str] = set()

    def create(self, filename: str) -> Py7zIO:
        member = self._extract_filename_to_member.get(filename)
        if member is None:
            logger.error("Member %s not found", filename)
            return py7zr.io.NullIO()
        if member.is_link:
            logger.debug("Extracting link %s", filename)
            return ExtractLinkWriter(member)
        if not member.is_file:
            logger.debug("Ignoring non-file member %s", filename)
            return py7zr.io.NullIO()

        full_path = os.path.join(self._path, filename)
        if os.path.lexists(full_path) or full_path in self.outfiles:
            full_path += f"_{member.member_id}"

        self.member_id_to_outfile[member.member_id] = full_path
        self.outfiles.add(full_path)

        logger.debug("Creating writer for %s, path=%s", filename, full_path)
        return ExtractFileWriter(full_path)


class SevenZipReader(BaseArchiveReader):
    """Reader for 7-Zip archives."""

    _password_lock: Lock = Lock()

    def _translate_exception(self, e: Exception) -> Optional[ArchiveError]:
        if py7zr is not None:
            # Archive open
            if isinstance(e, py7zr.Bad7zFile):
                return ArchiveCorruptedError("Invalid 7-Zip archive")
            # Archive open or member read
            if isinstance(e, py7zr.PasswordRequired):
                return ArchiveEncryptedError("Password required")

            if isinstance(e, py7zr.exceptions.UnsupportedCompressionMethodError):
                return ArchiveUnsupportedFeatureError("Unsupported compression method")

            # Member read
            if isinstance(e, py7zr.exceptions.ArchiveError):
                return ArchiveError(f"Error reading archive: {e}")

        # Archive open (corrupted data or wrong password when decrypting the header)
        if isinstance(e, TypeError) and "Unknown field" in str(e):
            return ArchiveCorruptedError("Corrupted header data or wrong password")

        # Archive open
        if isinstance(e, EOFError):
            return ArchiveEOFError("Truncated 7-Zip archive")

        # Archive open or member read
        if isinstance(e, lzma.LZMAError):
            return ArchiveCorruptedError("Corrupted input data or wrong password")

        # Archive open (truncated data while reading a struct)
        if isinstance(e, struct.error):
            return ArchiveEOFError("Possibly truncated 7-Zip archive")

        # Archive open (invalid value in some field)
        if isinstance(e, IndexError):
            return ArchiveCorruptedError("Invalid 7-Zip archive")

        return None

    @contextmanager
    def _temporary_password(self, pwd: bytes | str | None):
        """Temporarily set the password for all folders in the archive."""
        if pwd is None or self._archive is None:
            yield
            return

        SevenZipReader._password_lock.acquire()
        try:
            folders = []
            try:
                folders = []
                if (
                    self._archive.header
                    and self._archive.header.main_streams
                    and self._archive.header.main_streams.unpackinfo
                ):
                    folders = self._archive.header.main_streams.unpackinfo.folders
            except AttributeError:
                folders = []

            previous = [f.password for f in folders]
            for f in folders:
                f.password = bytes_to_str(pwd)

            try:
                yield
            finally:
                for f, p in zip(folders, previous):
                    f.password = p
        finally:
            if pwd is not None:
                SevenZipReader._password_lock.release()

    def __init__(
        self,
        archive_path: BinaryIO | str,
        format: ArchiveFormat,
        *,
        pwd: bytes | str | None = None,
        streaming_only: bool = False,
    ):
        if format != ArchiveFormat.SEVENZIP:
            raise ValueError(f"Unsupported archive format: {format}")

        super().__init__(
            format=format,
            archive_path=archive_path,
            streaming_only=streaming_only,
            members_list_supported=True,
            pwd=pwd,
        )
        if is_stream(self.path_or_stream) and not is_seekable(self.path_or_stream):
            # SevenZipFile._real_get_contents() advances the stream to the second
            # header in the file, so it doesn't work with non-seekable streams.
            raise ArchiveStreamNotSeekableError(
                "7-Zip archives do not support non-seekable streams"
            )
        self._format_info: ArchiveInfo | None = None
        self._streaming_only = streaming_only

        if py7zr is None:
            raise PackageNotInstalledError(
                "py7zr package is not installed. Please install it to work with 7-Zip archives."
            )

        def _open_7z() -> py7zr.SevenZipFile:
            return py7zr.SevenZipFile(archive_path, "r", password=bytes_to_str(pwd))

        self._archive: py7zr.SevenZipFile | None = run_with_exception_translation(
            _open_7z,
            self._translate_exception,
            archive_path=str(archive_path),
        )

    def _close_archive(self) -> None:
        """Close the archive and release any resources."""
        self._archive.close()  # type: ignore
        self._archive = None

    def _is_member_encrypted(self, file: ArchiveFile) -> bool:
        # This information is not directly exposed by py7zr, so we need to use an
        # internal function to infer it.
        if file.folder is None:
            return False

        return py7zr.compressor.SupportedMethods.needs_password(file.folder.coders)

    def iter_members_for_registration(self) -> Iterator[ArchiveMember]:
        assert self._archive is not None

        name_counters: collections.defaultdict[str, int] = collections.defaultdict(int)
        links_to_resolve = {}

        for file in self._archive.files:
            # py7zr renames duplicate files when extracting by appending a
            # ``_<n>`` suffix to the later occurrences.  When we stream files
            # through a custom ``WriterFactory`` we receive those renamed
            # filenames, so we need to map them back to the actual archive
            # members.  Replicate py7zr's naming logic to build this mapping.

            count = name_counters[file.filename]
            if count == 0:
                extract_filename = file.filename
            else:
                extract_filename = f"{file.filename}_{count - 1}"

            name_counters[file.filename] += 1

            # 7z format doesn't include the trailing slash for directories, so we need
            # to add them for consistent behavior.
            filename = file.filename
            if file.is_directory and not filename.endswith("/"):
                filename += "/"
            file_type = (
                MemberType.DIR
                if file.is_directory
                else MemberType.SYMLINK
                if file.is_symlink
                else MemberType.OTHER
                if file.is_junction or file.is_socket
                else MemberType.FILE
            )
            crc32 = (
                file.crc32
                if file.crc32 is not None
                else 0
                if (file_type == MemberType.FILE and file.uncompressed == 0)
                else None
            )

            # py7zr stores Windows FILE_ATTRIBUTE_* in the upper bits of its
            # internal 'attributes' field, combined with a Unix-extension flag
            # (0x8000) and POSIX mode (bits 16+).  We expose windows_attrs only
            # when the Unix extension bit is absent, meaning the archive was
            # created on Windows.
            _7z_attrs: int | None = file._get_property("attributes")
            _7z_windows_attrs = (
                _7z_attrs & 0x07FFF
                if _7z_attrs is not None and not (_7z_attrs & 0x8000)
                else None
            )

            member = ArchiveMember(
                filename=filename,
                raw_filename=file.filename,
                # The uncompressed field is wrongly typed in py7zr as list[int].
                # It's actually an int.
                file_size=file.uncompressed,  # type: ignore
                compress_size=file.compressed,
                mtime_with_tz=py7zr.helpers.filetime_to_dt(file.lastwritetime)
                if file.lastwritetime
                else None,
                type=file_type,
                # link_target_type=
                mode=file.posix_mode,
                crc32=crc32,
                compression_method=None,  # Not exposed by py7zr
                encrypted=self._is_member_encrypted(file),
                windows_attrs=_7z_windows_attrs,
                raw_info=file,
                extra={
                    "extract_filename": extract_filename,
                },
            )

            if member.is_link:
                links_to_resolve[member.filename] = member
            yield member

        if links_to_resolve and not self._streaming_only:
            try:
                for member, stream in self._extract_members_iterator(
                    members=list(links_to_resolve.values()),
                    pwd=None,
                ):
                    member.link_target = stream.read().decode("utf-8")
            except ArchiveError as e:
                logger.error("Error resolving links: %s", e)
                # Skip the links that failed to resolve, they'll just have an empty
                # link target.

        self._all_members_registered = True

    def _prepare_member_for_open(
        self, member: ArchiveMember, *, pwd: bytes | str | None, for_iteration: bool
    ) -> ArchiveMember:
        if pwd is not None and member.is_link and member.link_target is None:
            try:
                list(
                    self.iter_members_with_streams(
                        members=[member], pwd=pwd, close_streams=False
                    )
                )
            except (
                ArchiveError,
                py7zr.exceptions.ArchiveError,
                py7zr.PasswordRequired,
                lzma.LZMAError,
                OSError,
            ):
                pass
            if member.link_target is None:
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
        assert self._archive is not None

        it = list(
            self.iter_members_with_streams(
                members=[member], pwd=pwd, close_streams=False
            )
        )
        assert len(it) == 1, (
            f"Expected exactly one member, got {len(it)}. {member.filename}"
        )
        stream = cast("StreamingFile.Reader", it[0][1])
        if isinstance(stream, ErrorIOStream):
            stream.read()
        return stream

    def _build_extract_filename_to_member_map(
        self, members: list[ArchiveMember], path_str: str | None
    ) -> dict[str, ArchiveMember]:
        """Mimics the py7zr name sanitization logic in py7zr.SevenZipFile._extract()."""
        path = pathlib.Path(path_str) if path_str is not None else None
        if path is not None and not path.is_absolute():
            path = pathlib.Path(os.getcwd()).joinpath(path)

        return {
            py7zr.helpers.get_sanitized_output_path(
                member.extra["extract_filename"], path
            ).as_posix(): member
            for member in members
        }

    def _extract_members_iterator(
        self,
        members: list[ArchiveMember],
        pwd: bytes | str | None,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO]]:
        # We need to use the exact same sanitization logic as py7zr so we can match
        # the filenames passed to the StreamingFactory with the members.
        extract_filename_to_member = self._build_extract_filename_to_member_map(
            members, None
        )

        # The original filenames in the raw infos.
        extract_targets = [
            cast("ArchiveFile", member.raw_info).filename for member in members
        ]

        # Allow the queue to carry tuples, exceptions, or None
        q = Queue[tuple[str, BinaryIO] | Exception | None]()

        # TODO: check that all the requested files to extract() were actually
        # extracted exactly once.
        def extractor():
            try:
                assert self._archive is not None
                factory = StreamingFactory(q)
                with self._temporary_password(pwd):
                    self._archive.reset()
                    self._archive.extract(targets=extract_targets, factory=factory)
                    factory.finish()
            except Exception as e:  # noqa: BLE001
                # Here we do want to catch all exceptions, not just ArchiveError
                # subclasses, as any exception raised in this thread would be silently
                # ignored. We send them through the queue so that the main thread
                # doesn't wait forever, and can treat and/or re-raise them.
                q.put(e)

        thread = Thread(target=extractor)
        thread.start()

        logger.debug(
            "iter_members_iterator: starting -- targets: %s",
            extract_targets,
        )
        try:
            while True:
                item = q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    thread.join()
                    raise item
                fname, stream = item

                if fname not in extract_filename_to_member:
                    logger.warning(
                        "fname not in extract_filename_to_member: %s (names: %s)",
                        fname,
                        extract_filename_to_member.keys(),
                    )
                    continue

                member_info = extract_filename_to_member[fname]
                yield member_info, stream

            # TODO: the extractor may skip non-files or files with errors. Yield all remaining members. (but yield dirs before files?)
        except Exception as e:
            translated = self._translate_exception(e)
            if translated is not None:
                raise translated from e
            raise

        finally:
            thread.join()

    def iter_members_with_streams(
        self,
        members: Collection[ArchiveMember | str]
        | Callable[[ArchiveMember], bool]
        | None = None,
        *,
        pwd: bytes | str | None = None,
        filter: IteratorFilterFunc | ExtractionFilter | None = None,
        close_streams: bool = True,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        """Yield members and their streams using a worker thread and queue.

        ``py7zr`` expects a ``WriterFactory`` to collect extracted files at
        once. To provide a true iterator that lazily returns ``BinaryIO``
        streams, we spin up a background thread that performs the extraction and
        places each stream into a ``Queue``. This generator consumes from the
        queue so callers can process files as they are decompressed.
        """
        self.check_archive_open()
        assert self._archive is not None

        self._start_streaming_iteration()

        # Don't apply the filter now, as the link members may not have the extracted path.
        member_included_func = _build_member_included_func(members)
        filter_func = _build_filter(None, filter or self.config.extraction_filter, None)

        pending_files: list[ArchiveMember] = []
        pending_links_by_id: dict[int, ArchiveMember] = {}

        logger.debug("iter_members_with_streams: starting first pass")
        for member in self.iter_members():
            if not member_included_func(member):
                continue

            if member.is_link and member.link_target is None:
                # We'll need to resolve the link target later.
                pending_links_by_id[member.member_id] = member
                continue

            if member.is_file and member.file_size:  # Non-empty file
                pending_files.append(member)
                continue

            filtered_member = filter_func(member)
            if filtered_member is None:
                continue

            if member.is_dir or member.is_link:
                # TODO: accumulate all links to yield at the end, after resolving any
                # pending target members.
                yield filtered_member, None

            elif member.is_file:  # Empty file
                # Yield any empty files immediately, as py7zr doesn't actually call any
                # methods on the PyZ7IO object for them, and so they're not added to the
                # queue.
                stream = io.BytesIO(b"")
                yield filtered_member, stream
                if close_streams:
                    stream.close()

            else:
                logger.error(
                    f"Unknown member type: {member.type} for {member.filename}"
                )
                continue

        try:
            # Apply the filter to all the file members that may need to be extracted.
            # This is done here instead of above to make the filtering closer to when
            # the files are actually extracted.
            pending_files_by_id: dict[int, ArchiveMember] = {}
            for member in pending_files:
                filtered_member = filter_func(member)
                if filtered_member is not None:
                    pending_files_by_id[member.member_id] = filtered_member

            for member, stream in self._extract_members_iterator(
                members=list(pending_files_by_id.values())
                + list(pending_links_by_id.values()),
                pwd=pwd,
            ):
                if member.is_link:
                    # The links we extracted are the ones with the link target not yet set.
                    member.link_target = stream.read().decode("utf-8")

                else:
                    filtered_member = pending_files_by_id.pop(member.member_id)
                    yield filtered_member, stream
                    if close_streams:
                        stream.close()
        except ArchiveError as e:
            logger.error("Error in iter_members_with_streams: %s", e, exc_info=True)
            # Yield any remaining members that were not extracted, with the error.
            for member in pending_files_by_id.values():
                yield member, ErrorIOStream(e)

        for member in pending_links_by_id.values():
            filtered_member = filter_func(member)
            if filtered_member is not None:
                yield filtered_member, None

    def extract(
        self,
        member_or_filename: ArchiveMember | str,
        path: str | os.PathLike[str] | None = None,
        pwd: bytes | str | None = None,
    ) -> str:
        self.check_archive_open()
        assert self._archive is not None
        archive = self._archive

        member_obj = self.get_member(member_or_filename)

        def _do_extract() -> None:
            with self._temporary_password(pwd):
                archive.reset()
                archive.extract(path=path, targets=[member_obj.filename])

        run_with_exception_translation(
            _do_extract,
            self._translate_exception,
            archive_path=self.path_str,
            member_name=member_obj.filename,
        )

        return os.path.join(path or os.getcwd(), member_obj.filename)

    def _extract_pending_files(
        self, path: str, extraction_helper: ExtractionHelper, pwd: bytes | str | None
    ) -> None:
        pending_extractions = extraction_helper.get_pending_extractions()
        paths_to_extract = [member.filename for member in pending_extractions]
        # Perform a regular extraction
        assert self._archive is not None
        archive = self._archive

        pending_extractions_to_member = self._build_extract_filename_to_member_map(
            pending_extractions, path
        )
        factory = ExtractWriterFactory(path, pending_extractions_to_member)

        logger.info("Extracting %s to %s", paths_to_extract, path)

        def _do_extract() -> None:
            with self._temporary_password(pwd):
                archive.reset()
                archive.extract(
                    path, targets=paths_to_extract, recursive=False, factory=factory
                )

        run_with_exception_translation(
            _do_extract,
            self._translate_exception,
            archive_path=self.path_str,
        )
        logger.info("Extraction done")

        for member in pending_extractions:
            outfile = factory.member_id_to_outfile.get(member.member_id)
            extraction_helper.process_file_extracted(member, outfile)

    def _is_solid(self) -> bool:
        assert self._archive is not None
        if self._archive.header.main_streams is None:
            # There's a bug in py7zr that causes archiveinfo() to raise an exception
            # if the archive is empty or has no main streams, so avoid it here.
            return False

        return self._archive.archiveinfo().solid

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format.

        Returns:
            ArchiveInfo: Detailed format information
        """
        self.check_archive_open()
        assert self._archive is not None

        if self._format_info is None:
            self._format_info = ArchiveInfo(
                format=self.format,
                is_solid=self._is_solid(),
                extra={
                    "is_encrypted": self._archive.password_protected,
                },
            )
        return self._format_info

    @classmethod
    def is_7z_file(cls, file: BinaryIO | str) -> bool:
        if py7zr is not None:
            return py7zr.is_7zfile(file)
        return False
