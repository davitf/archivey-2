"""CLI exit codes (argparse-aligned)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAIL = 1  # operational failure / aborted extract (STOP-path failure / always-stop)
EXIT_USAGE = 2  # CLI usage error (argparse default)
# Q8 Option A: completed extract with ≥1 policy BLOCKED and no FAILED (safe members on disk).
# Applies under CONTINUE or STOP — STOP never aborts on a policy block.
EXIT_POLICY = 3
# ≥4 reserved
