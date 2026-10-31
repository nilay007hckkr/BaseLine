"""Which code produced a result file, so every number can be traced and re-run."""

from __future__ import annotations

import subprocess

from hindsight.core.config import ROOT


def code_sha() -> str:
    """The commit of this checkout, with "-dirty" when backend code has uncommitted changes (the
    hash alone would then not reproduce the run). Empty when git is not available."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "backend"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return ""
