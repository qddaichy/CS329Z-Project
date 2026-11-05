"""Install the state-only companion; --restart discards the running test session."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from project.paths import REPO_ROOT, MOD_ROOT, ENV_FILE


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if Path(sys.prefix).name.casefold() != "cs329z_project":
        raise SystemExit("Activate the cs329z_project Conda environment first.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--restore-core-backup", type=Path,
                        help="Restore a verified project-local core DLL backup before restarting.")
    args = parser.parse_args()
    import psutil
    from dotenv import dotenv_values

    project = REPO_ROOT
    result = json.loads((project / ".tools/mod-build/result.json").read_text())
    source = Path(result["dll"]).resolve()
    if not source.is_relative_to(project) or source.name != "StateOnlyMod.dll":
        raise SystemExit("Unexpected build output path.")
    if not source.is_file():
        raise SystemExit("Build output is missing; run python -m project.tools.build_mod first.")
    app = Path(dotenv_values(ENV_FILE)["STARDEW_APP_PATH"]).resolve()
    if app.parent != Path(result["game"]).resolve():
        raise SystemExit("Build and installation game paths differ.")
    installed = app.parent / "Mods/StarDojoStateOnly/StateOnlyMod.dll"
    running = []
    for process in psutil.process_iter(["name", "exe"]):
        if process.info["exe"] and Path(process.info["exe"]).resolve() == app:
            running.append(process)
    if running and not args.restart:
        raise SystemExit("Close SMAPI first, or use --restart for a disposable test session.")
    for process in running:
        print(f"Closing authorized test game process {process.pid}.", flush=True)
        process.terminate()
        process.wait(timeout=15)

    backups = project / ".tools/mod-build/backups"
    backups.mkdir(parents=True, exist_ok=True)
    if args.restore_core_backup:
        core = args.restore_core_backup.resolve()
        if not core.is_relative_to(backups) or core.name != f"StardojoMod.{digest(core)[:12]}.dll":
            raise SystemExit("Core backup path/hash was not verified.")
        shutil.copy2(core, app.parent / "Mods/StardojoMod/StardojoMod.dll")
        print("Restored the original prebuilt core DLL.", flush=True)
    backup = None
    if installed.exists():
        backup = backups / f"StateOnlyMod.{digest(installed)[:12]}.dll"
        if not backup.exists():
            shutil.copy2(installed, backup)
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, installed)
    shutil.copy2(MOD_ROOT / "manifest.json", installed.parent / "manifest.json")
    if digest(source) != digest(installed):
        raise SystemExit("Installed DLL hash differs from the build output.")
    report = {"installed": str(installed), "sha256": digest(installed),
              "backup": str(backup) if backup else None}
    (project / ".tools/mod-build/installed.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.restart:
        process = subprocess.Popen([str(app), "--port-id", str(args.port)], cwd=app.parent,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        report["pid"] = process.pid
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise SystemExit("SMAPI exited during startup; check its SMAPI log.")
            try:
                with socket.create_connection(("127.0.0.1", args.port), timeout=1):
                    break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("SMAPI did not open its local command port within 60 seconds.")
    (project / ".tools/mod-build/installed.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
