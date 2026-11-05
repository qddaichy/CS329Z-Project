# CS329Z: Planning agents in StarDojo

This repository compares ReAct, high-level planning, and reusable-tool strategies
in Stardew Valley. Our implementation lives in **[`project/`](project/README.md)**.
The current baseline selects one existing game API at a time and cannot execute
generated code or synthesize tools.

```text
CS329Z_project/
  project/                  CS329Z code, configuration, checks, and documentation
    agents/react.py         API-only ReAct agent and state evaluators
    evaluation/             Reusable farming task evaluators
    game/actions.py         Retained StarDojo API client
    run_react.py            ReAct experiment entry point
    run_high_level.py       High-level planning experiment entry point
    run_hybrid.py           Tool-synthesis experiment entry point
    experiment_runner.py    Shared experiment setup and reporting
    config/llm.json          Model configuration, without credentials
    .env                    Local credentials and game path (ignored by Git)
    data/                   Crafting recipes, benchmark task suites and saves
    tools/                  Game smoke test and companion mod build/install
    mod/StardojoMod/         Original StarDojo mod source
    mod/StateOnlyMod/        Our state-only companion mod source
    checks/                 Offline regression checks
    docs/                   Baseline and repository organization notes
    requirements.txt        Minimal project dependencies
    setup.ps1               Windows environment setup
  .tools/                   Local caches, DLL backups, cleanup archive (ignored)
  runs/                     Experiment trajectories and summaries (ignored)
```

## Environment configuration (Windows)

The local environment was checked on October 27, 2026:

| Component | Current configuration |
| --- | --- |
| Shell | Windows PowerShell |
| Python environment | Conda environment `cs329z_project`, Python **3.10.9** |
| Project dependencies | Pinned in `project/requirements.txt`; installed versions match |
| Game integration | SMAPI with the prebuilt StarDojoMod and our StateOnlyMod installed |
| Game API | Local port **10783**, state-only observations |
| Local environment variables | `project/.env`, containing the game executable path and model API key |
| Model configuration | `project/config/gemini_flash.json`, default Gemini `gemini-3.8-flash` |
| Mod build toolchain | Installed .NET SDK **8.0.403**; companion targets `net6.0` |

The game path is configured on the current machine. Add your Gemini API key
to `project/.env`; model access and available account balance need a live check.

Tinker is also supported: add `TINKER_API_KEY` to `project/.env` and pass
`--provider tinker` to run Qwen3.8-27B with low reasoning. This needs no additional
packages. See [Tinker setup and connection testing](project/docs/tinker.md).

### First-time setup

On a new machine, install Conda, Stardew Valley and SMAPI first, then run:

```powershell
cd C:\Project\CS329z-Project\CS329Z_project
# Create once; skip this command if the environment already exists.
conda create -n cs329z_project python=3.10.9 -y
conda activate cs329z_project
.\project\setup.ps1
```

The setup script installs `project/requirements.txt` into the activated environment.
The upstream Python agent framework and its larger dependency list were removed.

Create **`project/.env`** from `project/.env.example`, or edit the existing file:

```dotenv
STARDEW_APP_PATH=C:/path/to/Stardew Valley/StardewModdingAPI.exe
OPENROUTER_API_KEY=your-openrouter-api-key
GEMINI_API_KEY=your-gemini-api-key
```

`STARDEW_APP_PATH` points to the executable file. Keep credentials in the ignored
`.env`; default model settings are in `project/config/gemini_flash.json`.
The default uses Gemini 3.8 Flash at low reasoning. Use `--provider openrouter`
for the retained Terra profile. For direct OpenAI access, set `OA_OPENAI_KEY` (or
`OPENAI_API_KEY`) and pass `--llm-config project/config/openai.json` to the runner.

Install StarDojoMod into the game's `Mods/` directory. On a new machine, build and
install our state-only companion after installing the .NET SDK:

```powershell
python -m project.tools.build_mod --allow-downloads
# Close SMAPI before installing the companion DLL.
python -m project.tools.install_mod
```

Both mods are already installed on the current machine. Rebuild before reinstalling
after source changes or the recent directory move. .NET is used to build the mod;
running the Python baseline with the installed mods does not invoke the SDK.

The two source trees are in `project/mod/StardojoMod/` and
`project/mod/StateOnlyMod/`. On the current machine their installed DLLs are in
`C:\Game\Steam\steamapps\common\Stardew Valley\Mods\StardojoMod\` and
`C:\Game\Steam\steamapps\common\Stardew Valley\Mods\StarDojoStateOnly\`.
See [mod locations and responsibilities](project/docs/mods.md).

### Daily use

Open a terminal and run from the `CS329Z_project/` directory:

```powershell
cd C:\Project\CS329z-Project\CS329Z_project
conda activate cs329z_project

# Launch the game through SMAPI, load a save, and keep its window active.
python -m project.tools.control_smoke
python -m project.tools.inspect_context --menu --world --crops
python -m project.run_react --cut-grass 5 --max-steps 50 --show-live-log
python -m project.run_react --till-tiles 1 --show-live-log

# No game or model credentials required for offline checks.
python -m unittest discover -s project/checks -p "check_*.py"
```

SMAPI accepts `--port-id 10783` when selecting the game API port; use the same
`--port` in Python if choosing a different port. The smoke check does not call an
LLM; the ReAct runner sends model requests and acts on the currently loaded save.
The fishing minigame defaults to automatic success after 10 unpaused seconds.
The [baseline protocol](project/docs/react_baseline.md) explains how to restore
manual fishing and record the setting across experiments.

Select `cs329z_project` as the Python interpreter in VS Code. The Python entry
points resolve `project/.env` explicitly; no manual `PYTHONPATH` or global environment
variables are required. To check the terminal's selected interpreter:

```powershell
python -c "import sys; print(sys.version); print(sys.executable)"
```

The executable should be under `miniconda3\envs\cs329z_project\python.exe`.
See the [project guide](project/README.md) and
[baseline documentation](project/docs/react_baseline.md) for task evaluation and logs.

## Repository notes

Only the upstream API client, crafting recipes, benchmark task definitions/saves,
and original game mod source are retained under `project/`. The old `agent/`,
`env/`, and `docs/` directories, upstream runners, and broad dependency setup
were removed. The existing `.env` was moved intact to `project/.env`.
Local recovery archives are under `.tools/cleanup/`. See
[organization and cleanup details](project/docs/repository_layout.md).

StarDojo: [project website](https://stardojo2025.github.io/stardojo) ·
[retained API client](project/game/actions.py) · [license](LICENSE).
