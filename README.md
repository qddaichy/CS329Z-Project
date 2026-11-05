# CS329Z_project: Learning Agents in Stardew Valley

We compare three agents: **A** creates reusable skills, **B** adds daily planning
and reflection, and **C** adds structured economic beliefs. All use the same game
API, observations, and evaluation.

Run all commands from the repository root in **Windows PowerShell**.

## 1. Prerequisites

- **Stardew Valley**, launched once before installing mods.
- **[SMAPI](https://smapi.io/)** installed in the game directory
  ([Windows guide](https://stardewvalleywiki.com/Modding:Installing_SMAPI_on_Windows)).
- **[StarDojoMod](https://www.nexusmods.com/stardewvalley/mods/34175)**:
  extract the prebuilt download into the game's `Mods/` directory. Keep all bundled
  files and check that `Mods/StardojoMod/StardojoMod.dll` exists.
- **[Conda](https://www.anaconda.com/docs/getting-started/installation)** and
  **[.NET 8 SDK](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)**.
- An **OpenAI API key** with access to **GPT-6.1 Terra** (`gpt-6.1-terra`).

## 2. Python and credentials

Clone this repository, open a Conda-enabled PowerShell terminal in its root, and run:

```powershell
conda create -n cs329z_project python=3.10.9 -y
conda activate cs329z_project
python -m pip install --no-user -r project/requirements.txt
if (-not (Test-Path project/.env)) { Copy-Item project/.env.example project/.env }
```

Skip `conda create` if the environment already exists. Keep its name
`cs329z_project`, which the setup tools require. Edit `project/.env`:

```dotenv
STARDEW_APP_PATH=C:/path/to/Stardew Valley/StardewModdingAPI.exe
OA_OPENAI_KEY=your-openai-api-key
```

Use your actual game path and key. `.env` is ignored by Git. All commands below
use the direct OpenAI preset in [`project/config/openai.json`](project/config/openai.json).

## 3. Install our companion and start the game

Close Stardew Valley and SMAPI, then run:

```powershell
python -m project.tools.build_mod --allow-downloads
python -m project.tools.install_mod --restart
```

This builds and installs **StateOnlyMod**, then starts SMAPI on port **10783**.
Wait for the title screen and confirm both `StardojoMod` and `StarDojo State Only`
loaded successfully.

## 4. Run agent A, B, or C

| Agent | Behavior | Entry point |
| --- | --- | --- |
| **A** | Creates, tests, and reuses skills; no required daily plan or reflection | `project.run_hybrid` |
| **B** | A + daily planning, skill review, and free-form reflection | `project.run_hybrid_reflection` |
| **C** | B + structured economic beliefs, uncertainty, and evidence | `project.run_hybrid_economics` |

**Before each run**, prepare the same starting farm. This creates and loads a
separate save copy, initializes the experimental fixture, and sets cash to 500 gold:

```powershell
python -m project.tools.prepare_profit_test
```

Then choose **one** command. Each uses GPT-6.1 Terra and the same five-day cash
objective, with limits of 500 model decisions, 500 primitive actions, and 90 minutes:

```powershell
# Agent A: skill baseline
python -m project.run_hybrid --provider openai --profit-days 5 --max-steps 500 --max-primitive-actions 500 --max-seconds 5400

# Agent B: daily reflection
python -m project.run_hybrid_reflection --provider openai --profit-days 5 --max-steps 500 --max-primitive-actions 500 --max-seconds 5400

# Agent C: economic beliefs
python -m project.run_hybrid_economics --provider openai --profit-days 5 --max-steps 500 --max-primitive-actions 500 --max-seconds 5400
```

Run agents **one at a time**, reloading the fixture before switching agents.
Keep the game window active. Runs make billable API calls and may stop before
five in-game days if a budget is exhausted. Add `--show-live-log` for a separate
monitor window.

The terminal prints the report path under `runs/<strategy>/<timestamp>/`.
Inspect `summary.json` for cash, success, and stop reason; `trajectory.jsonl`
records actions and observations. For the five-day task, success means reaching
the deadline, not proving optimal profit. Hybrid runs also save `tools.json`,
`learning.json`, and `daily_evidence.json`.

## Optional quick checks

For a short ReAct connectivity demo instead of a multi-day experiment:

```powershell
python -m project.tools.control_smoke --load-test-save
python -m project.run_react --provider openai --cut-grass 1 --max-steps 30 --max-seconds 180
```

For offline tests without the game or an API key:

```powershell
python -m unittest discover -s project/checks -p "check_*.py"
```

Built on [StarDojo](https://github.com/StarDojo2025/stardojo).
The upstream [MIT license](LICENSE) is retained.
