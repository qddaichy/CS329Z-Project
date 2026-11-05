"""Build the state-only companion with project-local .NET/NuGet settings."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from project.paths import REPO_ROOT, MOD_ROOT, ENV_FILE


def main():
    if Path(sys.prefix).name.casefold() != "cs329z_project":
        raise SystemExit("Activate the cs329z_project Conda environment first.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-downloads", action="store_true",
                        help="Allow missing NuGet dependencies to be downloaded into .tools.")
    args = parser.parse_args()
    from dotenv import dotenv_values
    project = REPO_ROOT
    app_path = dotenv_values(ENV_FILE).get("STARDEW_APP_PATH")
    if not app_path or not Path(app_path).is_file():
        raise SystemExit("Set a valid STARDEW_APP_PATH in project/.env first.")
    game = Path(app_path).parent
    helper = game / "Mods/StardojoMod/StardojoMod.dll"
    if not helper.is_file():
        raise SystemExit("Install the prebuilt StarDojoMod package first.")

    work = project / ".tools/mod-build"
    work.mkdir(parents=True, exist_ok=True)
    config = work / "NuGet.Config"
    source = '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" />' if args.allow_downloads else ""
    config.write_text('<configuration><packageSources><clear />' + source +
                      '</packageSources></configuration>', encoding="utf-8")
    child_env = os.environ.copy()
    child_env.update({
        "DOTNET_CLI_HOME": str(project / ".tools/dotnet"),
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
        "DOTNET_ADD_GLOBAL_TOOLS_TO_PATH": "false",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true",
        "DOTNET_CLI_UI_LANGUAGE": "en-US",
        "NUGET_PACKAGES": str(project / ".tools/nuget"),
        "NUGET_HTTP_CACHE_PATH": str(work / "http-cache"),
        "NUGET_SCRATCH": str(work / "scratch"),
        "NUGET_PLUGINS_CACHE_PATH": str(work / "plugins-cache"),
    })
    command = ["dotnet", "build", str(MOD_ROOT / "StateOnlyMod.csproj"),
               "-c", "Release", "--nologo", f"-p:GamePath={game}",
               "-p:EnableModDeploy=false", "-p:EnableModZip=false",
               "-p:NuGetAudit=false", f"-p:RestoreConfigFile={config}",
               f"-p:RestorePackagesPath={project / '.tools/nuget'}"]
    needs_restore = not (MOD_ROOT / "obj/project.assets.json").is_file() or args.allow_downloads
    if not needs_restore:
        command.append("--no-restore")
    log = work / "build.log"
    user_config = Path(os.environ["APPDATA"]) / "NuGet/NuGet.Config"
    config_existed = user_config.exists() if needs_restore else True
    started = time.time()
    try:
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(command, cwd=project, env=child_env,
                                    stdout=output, stderr=subprocess.STDOUT)
    finally:
        # NuGet may generate its default user config even with RestoreConfigFile.
        # Restore its absence only if this invocation created an untouched default.
        if needs_restore and not config_existed and user_config.is_file():
            data = user_config.read_bytes()
            root = ET.fromstring(data)
            sources = list(root.findall("packageSources/add"))
            info = user_config.stat()
            if (len(list(root)) == 1 and len(sources) == 1
                    and sources[0].attrib == {"key": "nuget.org", "value": "https://api.nuget.org/v3/index.json", "protocolVersion": "3"}
                    and info.st_ctime >= started - 1 and info.st_mtime - info.st_ctime < 1):
                (work / "generated-user-NuGet.Config").write_bytes(data)
                user_config.unlink()
    if result.returncode:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(line for line in lines if ": error " in line) or "\n".join(lines[-20:]))
        raise SystemExit(f"Build failed. Full log: {log}")
    dll = max((MOD_ROOT / "bin/Release").rglob("StateOnlyMod.dll"),
              key=lambda path: path.stat().st_mtime)
    report = {"dll": str(dll), "game": str(game), "log": str(log)}
    (work / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Build succeeded; nothing was installed into the game.")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
