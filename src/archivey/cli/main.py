"""``archivey`` CLI entry point: argparse grammar + verb dispatch."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

import archivey
from archivey.cli.errors import CliError
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from archivey.cli.extract_cmd import run_extract
from archivey.cli.info_cmd import run_info
from archivey.cli.list_cmd import run_list
from archivey.cli.logging_config import configure_cli_logging
from archivey.cli.test_cmd import run_test
from archivey.exceptions import ArchiveyError

# Registered verbs + aliases + reserved unimplemented verbs (known-verb-wins).
_VERBS = frozenset(
    {
        "list",
        "l",
        "test",
        "t",
        "extract",
        "x",
        "info",
        "i",
        "detect",
        "hash",
        "create",
        "convert",
        "cat",
    }
)

# Options that take a following value (for default-list injection).
_VALUE_OPTIONS = frozenset(
    {
        "--password",
        "--overwrite",
        "--policy",
        "-d",
        "--dest",
        "--exclude",
    }
)


def _inject_default_list(argv: list[str]) -> list[str]:
    """If the first positional is not a known verb, insert ``list`` (known-verb-wins)."""
    i = 0
    skip_next = False
    while i < len(argv):
        if skip_next:
            skip_next = False
            i += 1
            continue
        tok = argv[i]
        if tok == "--":
            if i + 1 < len(argv) and argv[i + 1] not in _VERBS:
                return argv[: i + 1] + ["list"] + argv[i + 1 :]
            return argv
        # Bare "-" is the reserved stdin positional, not an option (F6).
        if tok.startswith("-") and tok != "-":
            key = tok.split("=", 1)[0]
            if key in _VALUE_OPTIONS and "=" not in tok:
                skip_next = True
            i += 1
            continue
        if tok not in _VERBS:
            return argv[:i] + ["list"] + argv[i:]
        return argv
    return argv


def _common_parent(*, suppress_defaults: bool) -> argparse.ArgumentParser:
    """Shared flags available before or after the verb.

    Build *two* instances (see ``build_parser``): the top-level copy carries real
    defaults; the subparser copy uses ``SUPPRESS`` so an absent post-verb flag cannot
    clobber a value the main parser already set (argparse shared-parents pitfall).
    """
    p = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    default_none: object = argparse.SUPPRESS if suppress_defaults else None
    default_false: object = argparse.SUPPRESS if suppress_defaults else False
    p.add_argument(
        "--password",
        default=default_none,
        help="archive password (prefer a TTY prompt; visible in process lists)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=default_false,
        help="more detail (per-member test/extract lines; list diagnostics)",
    )
    p.add_argument(
        "--hide-progress",
        action="store_true",
        default=default_false,
        help="suppress progress bars even when tqdm is installed",
    )
    p.add_argument(
        "--track-io",
        action="store_true",
        default=default_false,
        help=(
            "report decode/seek accounting "
            "(bytes decompressed, compressed consumed, seeks)"
        ),
    )
    return p


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "patterns",
        nargs="*",
        help="fnmatch include patterns (omit to select all members)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="fnmatch exclude pattern (repeatable; exclude wins over include)",
    )


def _add_salvage_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--salvage",
        action="store_true",
        help="reserved: best-effort reads (not implemented yet)",
    )


def build_parser() -> argparse.ArgumentParser:
    # Two parent instances: action objects are shared if the same instance is reused,
    # so SUPPRESS on a single parent would also wipe the main parser's defaults.
    common = _common_parent(suppress_defaults=False)
    common_sub = _common_parent(suppress_defaults=True)
    parser = argparse.ArgumentParser(
        prog="archivey",
        description=(
            "Inspect, verify, and safely extract archives. "
            "Bare invocation defaults to list. "
            "Forthcoming: hash, create, convert, cat."
        ),
        parents=[common],
        conflict_handler="resolve",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"archivey {archivey.__version__}",
    )

    sub = parser.add_subparsers(dest="verb", metavar="VERB")

    p_list = sub.add_parser(
        "list",
        aliases=["l"],
        parents=[common_sub],
        conflict_handler="resolve",
        help="list archive members (default verb)",
    )
    p_list.add_argument("archive", help="archive path")
    _add_filter_args(p_list)
    p_list.add_argument(
        "--digests",
        action="store_true",
        help="show stored member digests (no body read)",
    )
    _add_salvage_arg(p_list)
    p_list.set_defaults(_run="list")

    p_test = sub.add_parser(
        "test",
        aliases=["t"],
        parents=[common_sub],
        conflict_handler="resolve",
        help="full-read integrity check (verify stored digests)",
    )
    p_test.add_argument("archive", help="archive path")
    _add_filter_args(p_test)
    _add_salvage_arg(p_test)
    p_test.set_defaults(_run="test")

    p_extract = sub.add_parser(
        "extract",
        aliases=["x"],
        parents=[common_sub],
        conflict_handler="resolve",
        help="safely extract members",
    )
    p_extract.add_argument("archive", help="archive path")
    p_extract.add_argument(
        "-d",
        "--dest",
        default=None,
        help="destination directory (default: smart enclosing dir; use -d . for cwd)",
    )
    p_extract.add_argument(
        "--policy",
        choices=["strict", "standard", "trusted"],
        default="strict",
        help="extraction safety policy (default: strict)",
    )
    p_extract.add_argument(
        "--overwrite",
        choices=["error", "skip", "replace", "rename"],
        default="rename",
        help="collision policy (CLI default: rename; library default remains error)",
    )
    _add_filter_args(p_extract)
    _add_salvage_arg(p_extract)
    p_extract.set_defaults(_run="extract")

    p_info = sub.add_parser(
        "info",
        aliases=["i", "detect"],
        parents=[common_sub],
        conflict_handler="resolve",
        help="format detection + archive identity",
    )
    p_info.add_argument("archive", help="archive path")
    p_info.set_defaults(_run="info")

    for name, hint in (
        ("hash", "hash emission is not implemented yet"),
        ("create", "archive creation is not implemented yet"),
        ("convert", "archive conversion is not implemented yet"),
        ("cat", "member streaming to stdout is not implemented yet"),
    ):
        p = sub.add_parser(name, parents=[common_sub], help=f"reserved ({hint})")
        p.add_argument("archive", nargs="?", default=None)
        p.set_defaults(_run="reserved", _reserved_message=hint)

    return parser


def _dispatch(args: argparse.Namespace, *, out: TextIO, err: TextIO) -> int:
    run = getattr(args, "_run", None)
    if run is None:
        build_parser().print_help(err)
        return EXIT_USAGE

    if run == "reserved":
        raise CliError(getattr(args, "_reserved_message"), code=EXIT_USAGE)

    if run == "list":
        return run_list(
            archive=args.archive,
            password=args.password,
            track_io=bool(args.track_io),
            verbose=bool(args.verbose),
            patterns=list(args.patterns),
            exclude=list(args.exclude),
            digests=bool(args.digests),
            salvage=bool(args.salvage),
            out=out,
            err=err,
        )
    if run == "test":
        return run_test(
            archive=args.archive,
            password=args.password,
            track_io=bool(args.track_io),
            verbose=bool(args.verbose),
            patterns=list(args.patterns),
            exclude=list(args.exclude),
            salvage=bool(args.salvage),
            hide_progress=bool(args.hide_progress),
            out=out,
            err=err,
        )
    if run == "extract":
        return run_extract(
            archive=args.archive,
            password=args.password,
            track_io=bool(args.track_io),
            verbose=bool(args.verbose),
            dest=args.dest,
            patterns=list(args.patterns),
            exclude=list(args.exclude),
            policy=args.policy,
            overwrite=args.overwrite,
            salvage=bool(args.salvage),
            hide_progress=bool(args.hide_progress),
            out=out,
            err=err,
        )
    if run == "info":
        return run_info(
            archive=args.archive,
            password=args.password,
            track_io=bool(args.track_io),
            verbose=bool(args.verbose),
            out=out,
            err=err,
        )

    raise CliError(f"unknown verb {run!r}", code=EXIT_USAGE)


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """CLI entry point. Returns a process exit code."""
    out_stream = out if out is not None else sys.stdout
    err_stream = err if err is not None else sys.stderr
    raw = list(sys.argv[1:] if argv is None else argv)

    if not raw:
        build_parser().print_help(err_stream)
        return EXIT_USAGE

    argv_list = _inject_default_list(raw)
    parser = build_parser()
    try:
        args = parser.parse_args(argv_list)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return EXIT_OK
        if isinstance(code, int):
            return code
        print(code, file=err_stream)
        return EXIT_USAGE

    configure_cli_logging(verbose=bool(getattr(args, "verbose", False)), err=err_stream)

    try:
        return _dispatch(args, out=out_stream, err=err_stream)
    except CliError as exc:
        print(exc.message, file=err_stream)
        return exc.code
    except ArchiveyError as exc:
        print(exc, file=err_stream)
        return EXIT_FAIL
    except BrokenPipeError:
        # BrokenPipeError ⊂ OSError — must precede the OSError handler (F2).
        _silence_broken_pipe()
        return EXIT_OK
    except OSError as exc:
        print(exc, file=err_stream)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("interrupted", file=err_stream)
        return 130


def _silence_broken_pipe() -> None:
    """Avoid a secondary BrokenPipeError when the interpreter flushes closed pipes."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.close()
        except BrokenPipeError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
