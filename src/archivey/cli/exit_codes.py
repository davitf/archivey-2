"""CLI exit codes (argparse-aligned)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAIL = 1  # operational failure / aborted extract (incl. --stop-on-error)
EXIT_USAGE = 2  # CLI usage error (argparse default)
# CONTINUE completed with ≥1 policy BLOCKED and no FAILED (safe members on disk).
EXIT_POLICY = 3
# ≥4 reserved
