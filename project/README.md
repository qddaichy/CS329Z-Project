# CS329Z project code

Run the commands below from the repository directory, `CS329Z_project/`. Use Python
module entry points (`python -m project...`) so imports remain consistent. The
previous root scripts were moved here; root-level copies are no longer maintained.

## Setup

Use Python 3.10.9 and the `cs329z_project` Conda environment. For a new machine,
follow the [complete environment setup](../README.md#environment-configuration-windows).
Create the environment once, then activate it:

```powershell
conda create -n cs329z_project python=3.10.9 -y  # Skip if it already exists.
conda activate cs329z_project
.\project\setup.ps1
# Equivalent minimal dependency installation:
python -m pip install -r project/requirements.txt
```

The retained game client is `project/game/actions.py`; all benchmark assets are
under `project/data/`. The old upstream Python framework, runners and setup files
were removed. Install only `project/requirements.txt`.

Create **`project/.env`** from `.env.example`, or edit the existing file. The
previous `env/.env` was moved here with its contents unchanged:

```dotenv
STARDEW_APP_PATH=C:/path/to/Stardew Valley/StardewModdingAPI.exe
OPENROUTER_API_KEY=your-openrouter-api-key
```

The model defaults to `gemini-3.8-flash` via `project/config/gemini_flash.json`.
For the retained Terra profile use `--provider openrouter`; override its model ID via `--model`.
Credentials stay in the ignored `.env`, not in the JSON config. To use direct
OpenAI instead, set `OA_OPENAI_KEY` (or `OPENAI_API_KEY`) and run with
`--llm-config project/config/openai.json`.

## Game integration

Install the original prebuilt StarDojoMod into the game's `Mods/` directory.
Both mod sources are retained: `project/mod/StardojoMod/` is the original core;
`project/mod/StateOnlyMod/` is our state-only companion. See [mod locations](docs/mods.md).
To rebuild/install the companion:

```powershell
python -m project.tools.build_mod
# Close SMAPI before installing the companion DLL.
python -m project.tools.install_mod
```

The builder uses `.tools/` for local .NET/NuGet caches; use `--allow-downloads`
if dependencies are missing. Installation copies the companion DLL/manifest;
restart SMAPI after installing. Existing installed game mods were not changed
by the repository cleanup. Rebuild before installing after this directory move,
because previous build output paths are obsolete.

Launch SMAPI, load a save, and keep the game window active during movement.
The default API port is 10783:

```powershell
python -m project.tools.control_smoke
# Make a separate copy of the benchmark save and test one-tile movement.
python -m project.tools.control_smoke --load-test-save --move
# Scripted control check, without an LLM:
python -m project.tools.control_smoke --cut-grass 5
```

## ReAct experiments

```powershell
python -m project.run_react --cut-grass 5 --max-steps 50 --show-live-log
python -m project.run_react --collect-item Wood --quantity 10 --max-steps 80
python -m project.run_react --reach-location BusStop
python -m project.run_react --till-tiles 1 --show-live-log
python -m project.run_react --sow-seed "Parsnip Seeds" --quantity 1
python -m project.run_react --water-crops 1
python -m project.run_react --fertilize-with "Basic Fertilizer" --quantity 1
python -m project.run_react --money-net-gain 10000 --max-steps 300 --show-live-log
python -m project.run_react --pet-animals 1 --max-steps 100 --show-live-log
python -m project.run_react --defeat-monster "Bug" --quantity 1 --max-steps 100 --show-live-log
python -m project.run_react --list-tools
# Inspect game context without an LLM (requires the updated companion mod).
python -m project.tools.inspect_context --menu --world --crops --economy
python -m project.tools.inspect_context --entities --fishing
python -m project.tools.inspect_context --machines --recipes --quests
```

Fishing is simplified by default: after a bite starts the minigame, it succeeds
after 10 seconds of unpaused game time. See the [baseline protocol](docs/react_baseline.md)
for the manual-mode setting and how the rule is recorded in experiment results.

The runner acts on the current save. Grass removal, inventory gains, location and
farming changes are evaluated from state; a free-text goal without an evaluator has unverified
success. Runs write `config.json`, `trajectory.jsonl`, and `summary.json` under
`runs/react/`. See [baseline protocol and metrics](docs/react_baseline.md).
`--show-live-log` opens a live window with decisions and results; click the game
window to keep movement active. Close the log window when you are done viewing it.

Run all offline checks:

```powershell
python -m unittest discover -s project/checks -p "check_*.py"
```

## Adding strategies

The first planning experiment is available through the same runner:

```powershell
python -m project.run_high_level --money-balance 600 --model openai/gpt-6.1-terra --max-steps 100 --max-seconds 1200 --show-live-log
```

The planner proposes a short, revisable sequence of subgoals, each with an
observable verification condition, and refreshes it
every eight decisions by default (`--plan-interval`). Each action is still
selected separately through the same API allowlist and evaluated by the same
independent evaluator as ReAct. Plan calls and their token use are reported
separately and included in the `total_*` metrics; the comparison must count both
planning and action-selection calls. Plans appear in the live log and trajectory.
Output is saved under `runs/high_level/`. The planner is not allowed to write code
or synthesize tools. Use the same initial save, settings, model and budgets for
each strategy; a single successful run is not a success-rate estimate.
Both prompts prioritize reaching the verified goal quickly, including avoiding
unnecessary trips and combining compatible sales when practical. The subgoal
verification conditions are model-generated guidance, not independently enforced
evaluators; the existing evaluator alone determines overall task success.
`project.run_react` and `project.run_high_level` are separate entry points;
their shared setup, evaluation and logging live in `project/experiment_runner.py`.
The older `project.run_react --strategy high_level` command remains supported.

The hybrid experiment is started separately:

```powershell
python -m project.run_hybrid --money-balance 600 --model openai/gpt-6.1-terra --max-steps 100 --show-live-log
```

The agent may define a reusable tool as 2–6 existing API calls with bounded
parameters and an observable postcondition. Tool definitions are declarative
JSON, not executable Python. `test_tool` runs a candidate in the real game and
only enables reuse when its postcondition changes as specified; this demonstrates
one trial, not general correctness. Each primitive call is separately logged,
observed, and evaluated, and execution stops on failure, task success, or budget
exhaustion. A fresh episode starts with no tools; `tools.json` and
`trajectory.jsonl` under `runs/hybrid/` record definitions and trials. Model
decisions and primitive calls have separate limits; `--max-primitive-actions`
defaults to `--max-steps`. This first version excludes executable code, loops,
conditional branches, recursive tools and sleep dialogue inside tools. Recipes
can include bounded `wait` steps (50–10000 ms each), within the episode budget.
It retains normal single-step access to those supported actions. It cannot certify
that a model-chosen postcondition captures the intended meaning of a tool.
The A baseline acts on the overall goal and can create and reuse skills without a
daily plan or daily review. At the start of each observed in-game day, B and C
record a plan with a checkable goal, tool opportunities and daily/skill reviews;
C additionally maintains structured economic beliefs.
Large money goals may take multiple days; the agent plans achievable daily
milestones while preserving resources for future days. By default a money task
succeeds as soon as the balance condition is verified, including on the first day.
All strategies receive the same concise set of income opportunities: fishing,
foraging, crops, animals, mining and combat drops, processing, accepted quest
rewards, and selling existing goods. These are possibilities to evaluate, not
guaranteed availability: the agent must use the actual APIs, observe prerequisites,
compare costs and time, and verify income. Detailed game support and remaining
untested cases are described in [the baseline notes](docs/react_baseline.md).
For a money task that must span days, `--min-days 1` requires both the balance
target and one in-game day transition; this is checked by the evaluator.

Place new planning strategies in `project/agents/`, experiment settings in
`project/config/`, and regression checks in `project/checks/`. Reuse the same
game APIs, evaluators and budgets across strategies for meaningful comparisons.
`project/paths.py` locates retained benchmark data and `project/.env`. Details of the
retained upstream code and removed resources are in [the layout notes](docs/repository_layout.md).

## Hybrid daily-learning variations

All agents use compact-v3 tile grouping and explicit observed interaction-change
feedback. See [prompt efficiency and evaluation limitations](docs/prompt_efficiency.md).

Gemini 3.8 Flash is available with `--provider gemini` after setting
`GEMINI_API_KEY` in `project/.env`. The preset uses low reasoning; Gemini 3.8
Flash does not support disabling thinking. The adapter retains Gemini's tool-call
signatures across turns and accounts for provider-reported extra output tokens.
Run `python -m project.tools.check_gemini` for a small, billable no-game test.
For Gemini 3.5 Flash-Lite with minimal thinking, add
`--llm-config project/config/gemini_flash_lite.json` to `--provider gemini`.
`minimal` reduces thinking but does not guarantee zero thinking.

Tinker / Qwen3.8-27B is available to all strategies with `--provider tinker`.
Set `TINKER_API_KEY` in `project/.env`; run `python -m project.tools.check_tinker`
for a small no-game connection check. The preset uses low reasoning. Existing
Terra/OpenRouter remains available through `--provider openrouter`. See
[setup, commands and provider-recording details](docs/tinker.md).

Two separate entry points build on the existing hybrid agent:
`python -m project.run_hybrid_reflection` adds ordinary daily reflection;
`python -m project.run_hybrid_economics` adds evidence-linked economic estimates,
uncertainty and proposed tests. Both accept the usual goal/model/budget flags.
Agent C's version-3 harness requires numerical activity-level economic estimates
and an allocation plan before each observed day's actions. Next-morning cash
predictions and their scoring have been removed. Navigation now exposes
interior exits, verifies movement and stops repeated blocked moves. See the
[navigation and economic-review protocol](docs/navigation_and_forecasts.md).
The existing `project.run_hybrid` remains the control. All three now share a
bounded daily factual record and support `--disable-skill-synthesis` for a
controlled reflection comparison. Omit that flag to observe skill creation.
All three default to 2048 completion tokens per response to allow structured
reviews and recipes. See [commands, memory contracts and
experiment limitations](docs/hybrid_variations.md). Live logs show the latest
review; `learning.json` and `daily_evidence.json` retain review/evidence records.
The live monitor separates plan, skills and economic knowledge into three panels,
with selectable details, validation states and a daily-review tab. Failed skill
reuses now require retesting; unverified candidates can be repaired under the
same name and their revisions and execution outcomes are tracked.
