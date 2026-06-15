import argparse
import builtins
import fnmatch
import hashlib
import logging
import os
import sys
import zlib
from datetime import datetime
from importlib.metadata import version as package_version
from typing import IO, BinaryIO, Callable, Tuple, cast

from tqdm import tqdm

from archivey.archive_reader import ArchiveReader
from archivey.config import ArchiveyConfig, OverwriteMode
from archivey.core import open_archive
from archivey.exceptions import ArchiveError
from archivey.internal.dependency_checker import (
    format_dependency_versions,
    get_dependency_versions,
)
from archivey.internal.io_helpers import IOStats, StatsIO
from archivey.types import ArchiveMember, ExtractionFilter, MemberType

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO").upper())


def format_mode(member_type: MemberType, mode: int) -> str:
    permissions = mode & 0o777
    type_char = (
        "d"
        if member_type == MemberType.DIR
        else "l"
        if member_type == MemberType.SYMLINK
        else "h"
        if member_type == MemberType.HARDLINK
        else "-"
    )
    permissions_str = type_char
    letters = "xwr" * 3
    for bit in range(8, -1, -1):
        permissions_str += letters[bit] if permissions & (1 << bit) else "-"
    return permissions_str


def get_member_checksums(member_file: BinaryIO) -> Tuple[int, str]:
    crc32_value: int = 0
    sha256 = hashlib.sha256()
    for block in iter(lambda: member_file.read(65536), b""):
        crc32_value = zlib.crc32(block, crc32_value)
        sha256.update(block)
    return crc32_value & 0xFFFFFFFF, sha256.hexdigest()


def build_pattern_filter(patterns: list[str]) -> Callable[[ArchiveMember], bool] | None:
    """Create a filter function for member names based on shell-style patterns."""
    if not patterns:
        return None

    def _match(member: ArchiveMember) -> bool:
        return any(fnmatch.fnmatch(member.filename, pat) for pat in patterns)

    return _match


logger = logging.getLogger(__name__)


def process_member(
    member: ArchiveMember,
    archive: ArchiveReader,
    stream: BinaryIO | None = None,
    *,
    verify: bool,
    pwd: str | None = None,
) -> None:
    stream_to_close: BinaryIO | None = None

    encrypted_str = "E" if member.encrypted else " "
    size_str = "?" * 12 if member.file_size is None else f"{member.file_size:12d}"
    format_str = format_mode(member.type, member.mode or 0)

    if member.is_file:
        assert isinstance(member.filename, str)
        assert isinstance(member.mtime, datetime) or member.mtime is None

        try:
            if verify:
                if stream is None:
                    stream = stream_to_close = archive.open(member, pwd=pwd)
                crc32, sha256 = get_member_checksums(stream)
                if member.crc32 is not None and member.crc32 != crc32:
                    crc_error = f" != {member.crc32:08x}"
                else:
                    crc_error = ""
                sha = sha256[:16]
                crc_display = f"{crc32:08x}{crc_error}"
            else:
                crc_display = (
                    f"{member.crc32:08x}" if member.crc32 is not None else "?" * 8
                )
                sha = " " * 16
            print(
                f"{encrypted_str}  {size_str}  {format_str}  {crc_display}  {sha}  {member.mtime}  {member.filename.encode('utf-8', 'backslashreplace').decode('utf-8')}"
            )
        except ArchiveError as e:
            formatted_crc = (
                f"{member.crc32:08x}" if member.crc32 is not None else "?" * 8
            )
            print(
                f"{encrypted_str}  {size_str}  {format_str}  {formatted_crc}  {' ' * 16}  {member.mtime}  {member.filename} -- ERROR: {repr(e)}"
            )
        finally:
            if stream_to_close is not None:
                stream_to_close.close()
    elif member.is_link:
        assert isinstance(member.link_target, str) or member.link_target is None
        print(
            f"{encrypted_str}  {size_str}  {format_str}  {' ' * 8}  {' ' * 16}  {member.mtime}  {member.filename} -> {member.link_target}"
        )
    else:
        print(
            f"{encrypted_str}  {size_str}  {format_str}  {' ' * 8}  {' ' * 16}  {member.mtime}  {member.filename} {member.type.upper()}"
        )
    if member.comment:
        print(f"    Comment: {member.comment}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Archivey command line interface."
            " Use '--' followed by patterns to filter archive members."
        )
    )
    parser.add_argument("files", nargs="+", help="Archive files to process")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-t",
        "--test",
        action="store_const",
        const="test",
        dest="mode",
        help="List contents and verify checksums (default)",
    )
    mode.add_argument(
        "-l",
        "--list",
        action="store_const",
        const="list",
        dest="mode",
        help="List contents without verifying",
    )
    mode.add_argument(
        "-x",
        "--extract",
        action="store_const",
        const="extract",
        dest="mode",
        help="Extract files",
    )
    parser.set_defaults(mode="test")

    parser.add_argument(
        "--use-rar-stream",
        action="store_true",
        help="Use the RAR stream reader for RAR files",
    )
    parser.add_argument(
        "--use-rapidgzip",
        action="store_true",
        help="Use rapidgzip for reading gzip-compressed files",
    )
    parser.add_argument(
        "--use-indexed-bzip2",
        action="store_true",
        help="Use indexed_bzip2 for reading bzip2-compressed files",
    )
    parser.add_argument("--stream", action="store_true", help="Stream the archive")
    parser.add_argument(
        "--info", action="store_true", help="Print info about the archive"
    )
    parser.add_argument("--password", help="Password for encrypted archives")
    parser.add_argument(
        "--hide-progress", action="store_true", help="Hide progress bar"
    )
    parser.add_argument(
        "--use-stored-metadata",
        action="store_true",
        help="Use stored metadata for single file archives",
    )
    parser.add_argument(
        "--track-io",
        action="store_true",
        help="Track IO statistics for archive file access",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version and dependency information",
    )
    parser.add_argument(
        "--dest",
        default=".",
        help="Destination directory for extraction (default: current directory)",
    )
    parser.add_argument(
        "--overwrite-mode",
        choices=["overwrite", "skip", "error"],
        default="error",
        help="What to do when extracting files that already exist (default: error)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    pattern_args: list[str] = []
    if "--" in argv:
        idx = argv.index("--")
        pattern_args = argv[idx + 1 :]
        argv = argv[:idx]

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"archivey {package_version('archivey')}")
        versions = get_dependency_versions()
        print(format_dependency_versions(versions))
        return

    member_filter = build_pattern_filter(pattern_args)

    stats_per_file: dict[str, IOStats] = {}
    if args.track_io:
        original_open = builtins.open
        target_paths = {os.path.abspath(p) for p in args.files}

        def patched_open(file, mode="r", *oargs, **okwargs):
            path: str | None = None
            if isinstance(file, (str, bytes, os.PathLike)):
                path = str(os.path.abspath(os.fspath(file)))
            if (
                path is not None
                and path in target_paths
                and "r" in mode
                and not any(m in mode for m in ["w", "a", "+"])
            ):
                f = original_open(file, mode, *oargs, **okwargs)
                stats = stats_per_file.setdefault(path, IOStats())
                return StatsIO(cast("IO[bytes]", f), stats)
            return original_open(file, mode, *oargs, **okwargs)

        builtins.open = patched_open

    for archive_path in args.files:
        try:
            print(f"\nProcessing {archive_path}:")
            config = ArchiveyConfig(
                use_rar_stream=args.use_rar_stream,
                use_single_file_stored_metadata=args.use_stored_metadata,
                use_rapidgzip=args.use_rapidgzip,
                use_indexed_bzip2=args.use_indexed_bzip2,
                overwrite_mode=OverwriteMode[args.overwrite_mode.upper()],
            )
            with open_archive(
                archive_path,
                pwd=args.password,
                config=config,
                streaming_only=args.stream,
            ) as archive:
                print(f"Archive format: {archive.format} {archive.get_archive_info()}")
                if args.info:
                    continue

                verify = args.mode == "test"

                if args.mode == "extract":
                    archive.extractall(path=args.dest, members=member_filter)

                if args.stream:
                    members_if_available = archive.get_members_if_available()
                    if members_if_available is not None and member_filter is not None:
                        members_if_available = [
                            m for m in members_if_available if member_filter(m)
                        ]
                    for member, stream in tqdm(
                        archive.iter_members_with_streams(
                            members=member_filter, filter=ExtractionFilter.FULLY_TRUSTED
                        ),
                        desc="Computing checksums" if verify else "Listing members",
                        disable=args.hide_progress,
                        total=len(members_if_available)
                        if members_if_available is not None
                        else None,
                    ):
                        process_member(
                            member, archive, stream, verify=verify, pwd=args.password
                        )
                else:
                    members = archive.get_members()
                    if members is not None and member_filter is not None:
                        members = [m for m in members if member_filter(m)]
                    for member in tqdm(
                        members,
                        desc="Computing checksums" if verify else "Listing members",
                        disable=args.hide_progress,
                        total=len(members) if members is not None else None,
                    ):
                        process_member(
                            member, archive, verify=verify, pwd=args.password
                        )
        except ArchiveError as e:
            print(f"Error processing {archive_path}: {e}")
            logger.error("Error processing %s", archive_path, exc_info=True)
        if args.track_io:
            abs_path = os.path.abspath(archive_path)
            stats = stats_per_file.get(abs_path)
            if stats is not None:
                logger.info(
                    "IO stats for %s: %s bytes read, %s seeks",
                    archive_path,
                    stats.bytes_read,
                    stats.seek_calls,
                )

    if args.track_io:
        builtins.open = original_open  # type: ignore[has-type]


if __name__ == "__main__":  # pragma: no cover - manual execution
    main()
