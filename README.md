# CS329Z_project: Planning Agents in Stardew Valley

We compare ReAct, high-level planning, and agents that create reusable tools and
learn from daily experience. The agents control Stardew Valley through StarDojo's
game API and our structured-state companion mod.

**For instructors and TAs:** follow this page to run one small ReAct episode.
All commands below run from the repository root, where `README.md` and `project/`
are located. The setup below is for **Windows and PowerShell**.

## 1. Install prerequisites

- **Stardew Valley** on Windows. Run the unmodified game once before installing mods.
- **[SMAPI](https://smapi.io/)**, installed into the game directory. Follow its
  [Windows installation guide](https://stardewvalleywiki.com/Modding:Installing_SMAPI_on_Windows).
- **[StarDojoMod](https://www.nexusmods.com/stardewvalley/mods/34175)**:
  download the prebuilt mod from the Files tab (Nexus login may be required), and
  extract it into the game's `Mods/` folder. Check that
  `Mods/StardojoMod/StardojoMod.dll` exists. Keep the mod's other bundled files.
- **[Miniconda](https://www.anaconda.com/docs/getting-started/installation)**
  (or an existing Conda installation).
- **[.NET 8 SDK](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)**
  to build our companion mod. Install the **SDK**, not just the runtime.
- A **Gemini API key** with access to the model in
  [`project/config/gemini_flash.json`](project/config/gemini_flash.json).

The game, installed mods, and API credentials are not bundled in this repository.
Both StarDojoMod and our companion are required. No GPU is required by the Python agent.

## 2. Set up Python

Clone this repository with GitHub Desktop (or download and extract its ZIP), then
open a Conda-enabled PowerShell terminal in the repository root:

```powershell
conda create -n cs329z_project python=3.10.9 -y
conda activate cs329z_project
python -m pip install -r project/requirements.txt
if (-not (Test-Path project/.env)) { Copy-Item project/.env.example project/.env }
```

If the environment already exists, skip `conda create`. Keep the environment name
`cs329z_project`: the game setup tools check it. No additional Python packages,
editable installation, or `PYTHONPATH` changes are needed.

## 3. Configure and install our companion

Edit `project/.env`. Set the full path to **your installed SMAPI executable** and
your key; replace the example values below:

```dotenv
STARDEW_APP_PATH=C:/path/to/Stardew Valley/StardewModdingAPI.exe
GEMINI_API_KEY=your-gemini-api-key
```

Keep `.env` local; Git ignores it. The demo explicitly selects Gemini and uses the
model configured in `project/config/gemini_flash.json`. To use another accessible
Gemini model, add `--model YOUR_MODEL_ID` to the agent command in step 4.

Close Stardew Valley and SMAPI, then run:

```powershell
python -m project.tools.build_mod --allow-downloads
python -m project.tools.install_mod --restart
```

The first command builds **StateOnlyMod**, downloading missing build dependencies.
The second installs it into `Mods/StarDojoStateOnly/` and starts SMAPI on port
**10783**. Wait for the game to reach its title screen before continuing.

## 4. Run one agent

In the same terminal, create and load a separate copy of the included demo save:

```powershell
python -m project.tools.control_smoke --load-test-save
```

This setup command makes no model calls. It creates a timestamped save, loads it,
sets its starting balance to 500 gold, and checks game observations. It switches
away from any currently loaded save; use it with the game at the title screen.

Now run a **ReAct agent** with a small, independently checked goal: cut one grass tile.

```powershell
python -m project.run_react --provider gemini --cut-grass 1 --max-steps 30 --max-seconds 180
```

Click the **game window** and keep it active while the agent runs. Decisions and
observed progress appear in the terminal. This command makes billable model
requests and changes the demo save. It stops on verified success or its step/time
budget; model requests and actions can take additional time to finish.
Add `--show-live-log` if you want a separate log window.

The terminal prints the final result and report path. Each episode writes:

```text
runs/react/<timestamp>/
  config.json        Task, model, and run settings
  trajectory.jsonl   Decisions, actions, and observations
  summary.json       Verified success, stop reason, and usage
  live.log           Readable execution log
```

Look for `"success": true` in `summary.json`. A stopped run is not necessarily a
successful one; inspect `stop_reason` and any error. Repeat the save-loading
command before another demo to start from a fresh copy.

## Offline checks (no game or API key needed)

After step 2, you can check the agent logic without installing or launching the game:

```powershell
python -m unittest discover -s project/checks -p "check_*.py"
```

These checks use simulated game/model responses; they do not run a live episode.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `conda` is not recognized | Use a Conda-enabled PowerShell terminal, then activate `cs329z_project`. |
| `dotnet` is not recognized | Install the .NET 8 SDK and reopen the terminal. |
| `Install the prebuilt StarDojoMod package first` | Verify the core DLL is under the game folder specified in `project/.env`. |
| Connection refused / missing `shared_memory_10783.bin` | Start the game through SMAPI on port 10783; check the SMAPI console for mod loading errors. |
| State-only observations unavailable | Confirm both mods loaded, then restart SMAPI after installing the companion. |
| Missing key, authentication, or model-access error | Check `GEMINI_API_KEY` in `project/.env` and access to the configured model. |
| No movement / episode reaches its budget | Keep the game window active, close in-game menus, and reload the demo save before retrying. |

Built on [StarDojo](https://github.com/StarDojo2025/stardojo).
The upstream [MIT license](LICENSE) is retained.
