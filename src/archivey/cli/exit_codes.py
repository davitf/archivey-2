"""CLI exit codes (argparse-aligned)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAIL = 1  # operational failure (open/read/extract/integrity)
EXIT_USAGE = 2  # CLI usage error (argparse default)
EXIT_POLICY = 3  # extract completed with ≥1 safety-policy block, no FAILED
# ≥4 reserved
