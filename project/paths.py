"""Resolve repository assets independently of the caller's working directory."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent
ENV_FILE = PROJECT_ROOT / ".env"
DATA_ROOT = PROJECT_ROOT / "data"
MOD_ROOT = PROJECT_ROOT / "mod/StateOnlyMod"
CORE_MOD_ROOT = PROJECT_ROOT / "mod/StardojoMod"
