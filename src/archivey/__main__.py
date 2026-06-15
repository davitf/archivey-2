"""Entry point for ``python -m archivey``."""

from archivey.internal.cli import main as _cli_main


def main(argv: list[str] | None = None) -> None:
    """Run the :mod:`archivey` command line interface."""
    _cli_main(argv)


if __name__ == "__main__":  # pragma: no cover - manual entry
    main()
