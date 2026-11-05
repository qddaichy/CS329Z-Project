"""Shared experiment setup, evaluation and logging for API-only agents."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

from project.agents.react import FarmingEvaluator, GameTools, MoneyEvaluator, ReActAgent, StateEvaluator, run_episode, tool_schemas
from project.evaluation.animals_combat import PetEvaluator, CombatEvaluator
from project.evaluation.multiday import MultiDayEvaluator
from project.evaluation.profit_horizon import ProfitHorizonEvaluator
from project.agents.game_mechanics import MECHANICS_VERSION
from project.paths import REPO_ROOT, PROJECT_ROOT, DATA_ROOT, ENV_FILE


PROJECT = REPO_ROOT
HYBRID_STRATEGIES = ("hybrid", "hybrid_reflection", "hybrid_economics")


def build_agent(args, client, model):
    common = (client, model, args.history_turns, args.max_completion_tokens, args.temperature,
              args.observation_format)
    if args.strategy == "high_level":
        from project.agents.high_level import HighLevelAgent
        return HighLevelAgent(*common, plan_interval=args.plan_interval)
    if args.strategy in HYBRID_STRATEGIES:
        from project.agents.hybrid import HybridAgent
        from project.agents.hybrid_reflection import ReflectionHybridAgent
        from project.agents.hybrid_economics import EconomicsHybridAgent
        kind = {"hybrid": HybridAgent, "hybrid_reflection": ReflectionHybridAgent,
                "hybrid_economics": EconomicsHybridAgent}[args.strategy]
        return kind(*common, skill_synthesis=not args.disable_skill_synthesis)
    return ReActAgent(*common)


def api_key_for_config(config):
    """Never fall back to a different provider's key for an explicit key variable."""
    key_var = config.get("key_var", "OA_OPENAI_KEY")
    key = os.getenv(key_var)
    if not key and key_var in {"OA_OPENAI_KEY", "OPENAI_API_KEY"}:
        key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError(f"Set {key_var} in project/.env before running.")
    return key


def parse_args(argv=None, default_strategy="react"):
    parser = argparse.ArgumentParser(description=__doc__)
    task = parser.add_mutually_exclusive_group()
    task.add_argument("--goal", help="Free-text goal; add a verifier below for measured success.")
    task.add_argument("--task-name", "--task_name", help="Benchmark suite; supported evaluator types only.")
    parser.add_argument("--task-id", "--task_id", type=int, default=11)
    verification = parser.add_mutually_exclusive_group()
    verification.add_argument("--cut-grass", type=int, metavar="COUNT")
    verification.add_argument("--collect-item", metavar="NAME")
    verification.add_argument("--reach-location", metavar="NAME")
    verification.add_argument("--till-tiles", type=int, metavar="COUNT")
    verification.add_argument("--sow-seed", metavar="SEED_NAME")
    verification.add_argument("--water-crops", type=int, metavar="COUNT")
    verification.add_argument("--fertilize-with", metavar="FERTILIZER_NAME")
    verification.add_argument("--money-balance", type=int, metavar="GOLD")
    verification.add_argument("--money-net-gain", type=int, metavar="GOLD")
    verification.add_argument("--profit-days", type=int, metavar="DAYS",
                              help="Maximize cash over DAYS date transitions; stop after final overnight settlement.")
    verification.add_argument("--pet-animals", type=int, metavar="COUNT")
    verification.add_argument("--defeat-monster", metavar="NAME")
    parser.add_argument("--quantity", type=int, default=1, help="Count for collection, sowing, fertilizer, or monster tasks.")
    parser.add_argument("--min-days", type=int, default=0,
                        help="Require this many in-game day transitions as well as the money goal.")
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--radius", type=int, choices=(8,), default=8,
                        help="Agent observation radius (fixed at 8).")
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--max-seconds", type=float, default=600)
    parser.add_argument("--history-turns", type=int, default=8)
    parser.add_argument("--observation-format", choices=("compact", "raw"), default="compact",
                        help="Compact current tiles and past state, or send original full observations.")
    parser.add_argument("--settle-seconds", type=float, default=0.7)
    parser.add_argument("--no-pause", action="store_true", help="Let game time advance during LLM requests.")
    parser.add_argument("--provider", choices=('tinker', 'openrouter', 'gemini'), help="Select a provider preset without editing config files.")
    parser.add_argument("--llm-config", type=Path, default=None)
    parser.add_argument("--model", help="Override comp_model in the LLM config.")
    parser.add_argument("--strategy", choices=("react", "high_level", *HYBRID_STRATEGIES), default=default_strategy,
                        help="Agent variant; all use the same game APIs and evaluator.")
    parser.add_argument("--disable-skill-synthesis", action="store_true",
                        help="Disable define/test/run_tool for any hybrid variant to isolate reflection effects.")
    parser.add_argument("--plan-interval", type=int, default=8,
                        help="High-level planner refresh interval in agent decisions.")
    parser.add_argument("--max-primitive-actions", type=int, default=None,
                        help="Hybrid primitive API budget; defaults to --max-steps.")
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--max-completion-tokens", type=int, default=None,
                        help="Per-response limit (hybrid variants: 2048; other strategies: 512).")
    parser.add_argument("--request-timeout", type=float, default=45)
    parser.add_argument("--output-dir", type=Path, help="New directory for trajectory.jsonl and summary.json.")
    parser.add_argument("--show-live-log", action="store_true", help="Open a live on-screen log window.")
    parser.add_argument("--list-tools", action="store_true", help="Print tool schemas without connecting.")
    args = parser.parse_args(argv)
    if args.llm_config is None:
        args.llm_config = PROJECT_ROOT / 'config' / {'tinker': 'tinker_qwen.json', 'openrouter': 'llm.json', 'gemini': 'gemini_flash.json', None: 'gemini_flash.json'}[args.provider]
    if args.max_completion_tokens is None:
        args.max_completion_tokens = 2048 if args.strategy in HYBRID_STRATEGIES else 512
    if args.disable_skill_synthesis and args.strategy not in HYBRID_STRATEGIES:
        parser.error("--disable-skill-synthesis requires a hybrid strategy.")
    if not 1 <= args.radius <= 64 or not 1 <= args.port <= 65535:
        parser.error("Invalid radius or port.")
    if min(args.max_steps, args.history_turns, args.quantity, args.max_completion_tokens, args.plan_interval) < 1:
        parser.error("Step/history/token limits and quantity must be positive.")
    if min(args.max_seconds, args.request_timeout) <= 0 or args.settle_seconds < 0:
        parser.error("Time budgets must be positive; settle-seconds must be nonnegative.")
    if args.max_primitive_actions is not None and args.max_primitive_actions < 1:
        parser.error("--max-primitive-actions must be positive.")
    if args.min_days < 0 or (args.min_days and not (args.money_balance or args.money_net_gain)):
        parser.error("--min-days must be nonnegative and requires a money evaluator.")
    if args.cut_grass is not None and args.cut_grass < 1:
        parser.error("--cut-grass must be positive.")
    if any(value is not None and value < 1 for value in (args.till_tiles, args.water_crops, args.pet_animals)):
        parser.error("--till-tiles, --water-crops and --pet-animals must be positive.")
    if any(value is not None and value < 1 for value in (args.money_balance, args.money_net_gain)):
        parser.error("Money targets must be positive.")
    if args.profit_days is not None and args.profit_days < 1:
        parser.error("--profit-days must be positive.")
    if args.task_name and any(value is not None for value in (args.cut_grass, args.collect_item,
            args.reach_location, args.till_tiles, args.sow_seed, args.water_crops, args.fertilize_with,
            args.money_balance, args.money_net_gain, args.profit_days, args.pet_animals, args.defeat_monster)):
        parser.error("Benchmark tasks provide their own verifier.")
    return args


def task_settings(args):
    evaluator = None
    metadata = {"initial_state": "currently_loaded_save", "reset_performed": False}
    goal = args.goal
    if args.task_name:
        import yaml
        if not re.fullmatch(r"[a-z_]+", args.task_name):
            raise ValueError("Invalid task suite name.")
        path = DATA_ROOT / "task_suites" / (args.task_name + ".yaml")
        tasks = list(yaml.safe_load(path.read_text(encoding="utf-8")).items())
        if not 0 <= args.task_id < len(tasks):
            raise ValueError("Task ID is out of range.")
        goal, task = tasks[args.task_id]
        metadata.update(task_suite=args.task_name, task_id=args.task_id, benchmark_task=task,
                        fixture_note="Prepare the benchmark save and init_commands externally before running.")
        kind = task["evaluator"]
        if kind in {"harvest", "location"}:
            evaluator = StateEvaluator("collect" if kind == "harvest" else "location",
                                       str(task["object"]), task["quantity"])
        elif args.task_name in {"farming", "farming_lite"} and kind == "till" and task["object"] == "Tile":
            evaluator = FarmingEvaluator("till", count=task["quantity"])
        elif args.task_name in {"farming", "farming_lite"} and kind == "sow" and task["object"] == "Dirt":
            evaluator = FarmingEvaluator("sow", target=str(task["tool"]), count=task["quantity"])
        elif args.task_name in {"farming", "farming_lite"} and kind == "water" and task["object"] == "Crop":
            evaluator = FarmingEvaluator("water", count=task["quantity"])
        elif args.task_name in {"farming", "farming_lite"} and kind == "fertilize" and task["object"] == "Dirt":
            evaluator = FarmingEvaluator("fertilize", target=str(task["tool"]), count=task["quantity"])
        elif args.task_name in {"farming", "farming_lite"} and kind == "pet" and task["object"] == "Animal":
            evaluator = PetEvaluator(task["quantity"])
        else:
            raise ValueError(f"Unsupported benchmark evaluator or target: {kind} / {task['object']}.")
        goal = goal.replace("_", " ") + f". Target: {task['quantity']} {task['object']}."
    elif args.cut_grass:
        goal = goal or f"Cut at least {args.cut_grass} grass tiles using the scythe."
        evaluator = StateEvaluator("grass", count=args.cut_grass)
    elif args.collect_item:
        goal = goal or f"Collect {args.quantity} additional {args.collect_item}."
        evaluator = StateEvaluator("collect", args.collect_item, args.quantity)
    elif args.reach_location:
        goal = goal or f"Go to {args.reach_location}."
        evaluator = StateEvaluator("location", args.reach_location)
    elif args.till_tiles:
        goal = goal or f"Till {args.till_tiles} new tiles with the hoe."
        evaluator = FarmingEvaluator("till", count=args.till_tiles)
    elif args.sow_seed:
        goal = goal or f"Sow {args.quantity} tiles with {args.sow_seed}."
        evaluator = FarmingEvaluator("sow", args.sow_seed, args.quantity)
    elif args.water_crops:
        goal = goal or f"Water {args.water_crops} different crops that are not yet watered."
        evaluator = FarmingEvaluator("water", count=args.water_crops)
    elif args.fertilize_with:
        goal = goal or f"Fertilize {args.quantity} tiles using {args.fertilize_with}."
        evaluator = FarmingEvaluator("fertilize", args.fertilize_with, args.quantity)
    elif args.profit_days:
        objective = (f"Maximize final cash balance and net cash gain over {args.profit_days} in-game days through normal gameplay. "
                     f"The deadline is the first observed morning after {args.profit_days} date transitions from the start. "
                     "Use the available days efficiently; do not stop at an arbitrary gold target or sleep merely to finish sooner. "
                     "Plan investments, necessary maintenance and compatible earning activities around the remaining horizon. "
                     "Account for purchases, travel, energy, selling and payment timing. Sell or ship in time: the final overnight "
                     "shipping settlement counts; unsold inventory and unfinished investments are recorded separately, not counted as cash. "
                     "Follow the evaluator's remaining_game_days. No actions on the deadline morning are allowed. "
                     "Do not call finish before the deadline; the evaluator stops the run automatically. "
                     "Completion means the horizon was reached, not that profit is optimal.")
        goal = objective + (" Additional task instructions: " + goal if goal else "")
        evaluator = ProfitHorizonEvaluator(args.profit_days)
        metadata.update(objective="fixed_horizon_cash", required_game_days=args.profit_days)
    elif args.money_balance:
        goal = goal or f"Reach a balance of at least {args.money_balance} gold through normal gameplay, earning as efficiently as possible and minimizing time to the verified target."
        evaluator = MoneyEvaluator("balance", args.money_balance)
    elif args.money_net_gain:
        goal = goal or f"Increase your gold balance by at least {args.money_net_gain} through normal gameplay, earning as efficiently as possible and minimizing time to the verified target."
        evaluator = MoneyEvaluator("net_gain", args.money_net_gain)
    elif args.pet_animals:
        goal = goal or f"Pet {args.pet_animals} different farm animals."
        evaluator = PetEvaluator(args.pet_animals)
    elif args.defeat_monster:
        goal = goal or f"Defeat {args.quantity} {args.defeat_monster} monsters."
        evaluator = CombatEvaluator(args.defeat_monster, args.quantity)
    if args.min_days:
        evaluator = MultiDayEvaluator(evaluator, args.min_days)
        goal += f" Also advance at least {args.min_days} in-game day(s) through normal gameplay."
    if not goal:
        raise ValueError("Specify a goal, verifier, or benchmark task; see --help.")
    return goal, evaluator, metadata


def main(argv=None, default_strategy="react"):
    args = parse_args(argv, default_strategy=default_strategy)
    if args.list_tools:
        schemas = tool_schemas()
        if args.strategy in HYBRID_STRATEGIES:
            from project.agents.hybrid import extra_schemas
            schemas += extra_schemas(build_agent(args, None, "schema-only").extra_tool_specs())
        print(json.dumps(schemas, indent=2))
        return 0
    goal, evaluator, metadata = task_settings(args)
    from dotenv import load_dotenv
    from project.llm_client import create_client, ADAPTER_VERSION
    load_dotenv(ENV_FILE)
    config = json.loads(args.llm_config.read_text(encoding="utf-8"))
    if args.provider and args.provider != config.get('provider', 'openrouter'):
        raise ValueError('--provider conflicts with --llm-config; choose a matching provider config.')
    if config.get("is_azure"):
        raise ValueError("This baseline expects a non-Azure Chat Completions endpoint.")
    key = api_key_for_config(config)
    client = create_client(config, key, args.request_timeout)
    model = args.model or config["comp_model"]
    from project.game.actions import ActionProxy, _crafting_recipes

    class RecordedProxy(ActionProxy):
        # Existing wrappers sometimes return None even on success. Check the raw
        # transport here so connection errors cannot become successful actions.
        def __init__(self, port):
            super().__init__(port)
            self.wire_calls = 0
            self.wire_response_bytes = 0
            self.pending = []

        def _post_message(self, message, print_message=True):
            tick = time.perf_counter()
            self.wire_calls += 1
            reply = super()._post_message(message, print_message)
            self.wire_response_bytes += len(reply.encode("utf-8")) if isinstance(reply, str) else 0
            self.pending.append({"command": message, "seconds": time.perf_counter() - tick,
                                 "response_bytes": len(reply.encode("utf-8")) if isinstance(reply, str) else 0})
            if reply is None:
                raise ConnectionError(f"No response to {message.split('%')[0]}; action outcome is unknown.")
            if reply.strip().casefold().startswith(("error", "exception")):
                raise RuntimeError(reply[:500])
            return reply

    with socket.create_connection(("127.0.0.1", args.port), timeout=5):
        pass
    proxy = RecordedProxy(args.port)
    game = GameTools(proxy, args.radius, _crafting_recipes["content"].keys())
    agent = build_agent(args, client, model)
    output = args.output_dir or PROJECT / "runs" / args.strategy / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output.mkdir(parents=True, exist_ok=False)
    if hasattr(client, 'trace'):
        def trace_provider(event):
            with (output / 'provider_io.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        client.trace = trace_provider
    settings = {**vars(args), **metadata, "model": model, "goal": goal,
                "llm_provider": config.get('provider', 'openai'),
                "llm_base_url": config.get('base_url'),
                "llm_api_style": config.get('api_style', 'anthropic' if config.get('provider') == 'tinker' else 'openai'),
                "llm_thinking": config.get('thinking'),
                "llm_reasoning_effort": config.get('reasoning_effort'),
                "llm_adapter_version": ADAPTER_VERSION,
                "llm_max_attempts": config.get('max_attempts', 1),
                "game_mechanics_version": MECHANICS_VERSION,
                "navigation_version": 2,
                "interaction_feedback_version": 1,
                "observation_encoding": "compact_v3" if args.observation_format == "compact" else "raw",
                "started_at": datetime.now(timezone.utc).isoformat(), "tool_names": list(game.calls) + list(game.observations) + ["observe_state", "request_sleep", "confirm_sleep", "level_up_menu", "fish_input", "fish_hook", "fish_collect", "animal_menu", "animal_pan", "pet_animal", "carpenter", "open_quests", "quest_menu", "wait", "finish"]}
    if args.strategy in HYBRID_STRATEGIES:
        settings["tool_names"] += list(agent.extra_tool_specs())
        settings["reflection_mode"] = agent.reflection_mode
        settings["daily_evidence_version"] = 1
        settings["hybrid_workflow_version"] = 9
        if args.strategy == 'hybrid_economics':
            settings['economic_review_version'] = agent.review_version
            settings['daily_review_failure_limit'] = agent.review_failure_limit
    (output / "config.json").write_text(json.dumps(settings, indent=2, default=str), encoding="utf-8")
    print("Running against the currently loaded save. Keep the game window active.", flush=True)
    if args.task_name:
        print(metadata["fixture_note"], flush=True)
    print("Goal:", goal, "\nOutput:", output, flush=True)
    with client, (output / "trajectory.jsonl").open("w", encoding="utf-8") as trajectory, \
            (output / "live.log").open("w", encoding="utf-8", buffering=1) as live_log:
        def display(*args):
            print(*args, flush=True)
            print(*args, file=live_log, flush=True)

        display("Model:", model, "| Goal:", goal)
        if args.show_live_log:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            subprocess.Popen([sys.executable, "-m", "project.tools.log_viewer", str(output / "live.log")],
                             cwd=PROJECT, creationflags=flags)

        live_status = {"model": model, "strategy": args.strategy, "goal": goal, "step": 0, "phase": "starting",
                       "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                       "skill_synthesis": not args.disable_skill_synthesis if args.strategy in HYBRID_STRATEGIES else False}
        def emit(event):
            event["wire_commands"] = list(proxy.pending)
            proxy.pending.clear()
            if event["type"] == "initial_observation":
                settings["fishing_rules"] = event["state"].get("fishing_rules")
                settings["clock_rules"] = event["state"].get("clock_rules")
                settings["cutscene_rules"] = event["state"].get("cutscene_rules")
                (output / "config.json").write_text(json.dumps(settings, indent=2, default=str), encoding="utf-8")
            trajectory.write(json.dumps(event, ensure_ascii=False) + "\n")
            trajectory.flush()
            live_status["step"] = event.get("step", live_status["step"])
            live_status["phase"] = event["type"]
            live_status["updated_at"] = datetime.now(timezone.utc).isoformat()
            if event.get("usage"):
                for key in ("prompt_tokens", "completion_tokens"):
                    live_status["usage"][key] += event["usage"].get(key, 0)
            if event["type"] == "decision":
                live_status["decision"] = {key: event.get(key) for key in ("action", "arguments", "reason")}
            if event.get("state"):
                player = event["state"].get("player", {})
                live_status["game"] = {"location": player.get("location"), "money": player.get("money"),
                                       "time": event["state"].get("gamestate"), "progress": event.get("evaluation")}
            if getattr(agent, "is_hybrid", False):
                live_status["plan"] = agent.daily_plans[-1] if agent.daily_plans else None
                live_status["skills"] = agent.library_snapshot()
                learning = agent.learning_snapshot()
                live_status["learning"] = {"mode": learning["mode"],
                                           "latest_review": learning["reviews"][-1] if learning["reviews"] else None,
                                           "review_count": len(learning["reviews"]),
                                           "latest_general_reflection": (learning.get('general_reflections') or [None])[-1]}
                if event.get("state"):
                    live_status["workflow"] = agent.workflow_status(event["state"])
            elif getattr(agent, "plan", None) is not None:
                live_status["plan"] = agent.plan
            if event["type"] == "summary":
                live_status["result"] = {key: event.get(key) for key in ("success", "stop_reason", "error")}
                live_status["usage"] = {"prompt_tokens": event.get("total_prompt_tokens", event.get("prompt_tokens", 0)),
                                        "completion_tokens": event.get("total_completion_tokens", event.get("completion_tokens", 0))}
            if args.show_live_log:
                status_tmp = output / "live_status.tmp"
                try:
                    status_tmp.write_text(json.dumps(live_status, ensure_ascii=False, default=str), encoding="utf-8")
                    status_tmp.replace(output / "live_status.json")
                except OSError:
                    pass  # A viewer read must never interrupt the experiment.
            if event["type"] == "thinking":
                display(f"\nStep {event['step']}: waiting for model decision...")
            elif event["type"] == "waiting_for_day":
                display("  Waiting for overnight settlement...")
            elif event["type"] == "plan":
                display("  High-level plan:", json.dumps(event["plan"], ensure_ascii=False))
            elif event["type"] == "tool_primitive":
                display("  Tool step:", event["action"], json.dumps(event["arguments"], ensure_ascii=False),
                        "=>", json.dumps(event["result"], ensure_ascii=False))
            elif event["type"] == "decision":
                display(f"  Reason: {event['reason']}\n  Decision: {event['action']} "
                        f"{json.dumps(event['arguments'], ensure_ascii=False)}")
            elif event["type"] == "step":
                if event.get('review_rejection'):
                    display('  Daily economic review rejected:', json.dumps(event['review_rejection'], ensure_ascii=False))
                if "error" in event:
                    display("  Rejected:", event["error"])
                elif "result" in event:
                    display("  API result:", json.dumps(event["result"], ensure_ascii=False))
                    if event.get("action") in {"set_daily_plan", "update_economic_knowledge"} and event["result"].get("review"):
                        display("  Daily review:", json.dumps(event["result"]["review"], ensure_ascii=False))
                elif "skipped" in event:
                    display("  Skipped:", event["skipped"])
            if event["type"] in {"initial_observation", "step"} and "state" in event:
                player = event["state"]["player"]
                grass = sum(str(tile.get("terrain_at_tile", "")).rsplit(".", 1)[-1] == "Grass"
                            for tile in event["state"]["surroundingsdata"])
                display("  Observation:", json.dumps({
                    "location": player.get("location"), "position": player.get("position"),
                    "selected": player.get("currentinventory"), "stamina": player.get("stamina"),
                    "money": player.get("money"),
                    "visible_grass": grass, "game_time": event["state"].get("gamestate"),
                    "progress": event.get("evaluation")}, ensure_ascii=False))
        summary = run_episode(agent, game, goal, evaluator, args.max_steps, args.max_seconds,
                              args.settle_seconds, not args.no_pause, emit,
                              max_primitive_actions=args.max_primitive_actions)
        summary.update(wire_calls=proxy.wire_calls, wire_response_bytes=proxy.wire_response_bytes, **metadata)
        if hasattr(client, 'usage_incomplete_count'):
            summary.update(provider_requests=client.request_count,
                           provider_usage_complete=client.usage_incomplete_count == 0,
                           provider_requests_without_normalized_usage=client.usage_incomplete_count)
        display("\nResult:", json.dumps(summary, indent=2, ensure_ascii=False))
        display("Report:", output / "summary.json")
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.strategy in HYBRID_STRATEGIES:
        (output / "tools.json").write_text(json.dumps(agent.library_snapshot(), indent=2, ensure_ascii=False), encoding="utf-8")
        (output / "learning.json").write_text(json.dumps(agent.learning_snapshot(), indent=2, ensure_ascii=False), encoding="utf-8")
        (output / "daily_evidence.json").write_text(json.dumps(agent.experience.export(), indent=2, ensure_ascii=False), encoding="utf-8")
    client.close()
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    print("Report:", output / "summary.json", flush=True)
    return 0 if summary["success"] is True or (summary["success"] is None and summary["stop_reason"] == "agent_finished") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error
