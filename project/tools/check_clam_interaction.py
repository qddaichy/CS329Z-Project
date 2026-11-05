"""Test original interact on each side of the same clam, without an LLM.

Each case loads a new copy of the benchmark save. The test-only warp command
is used only for fixture positioning; pickup itself uses the normal agent API.
"""
import argparse
import json
import subprocess
import sys
import time

from project.agents.react import GameTools
from project.game.actions import ActionProxy
from project.paths import REPO_ROOT


def clam_count(state):
    return sum(item.get("quantity") or 0 for item in state["player"].get("inventory", [])
               if str(item.get("name") or "").casefold() == "clam")


def clam_on_ground(state):
    return any(tile.get("position") == [25, 7] and
               str(tile.get("object_at_tile") or "").casefold() == "clam"
               for tile in state["surroundingsdata"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--selected-slot", type=int, choices=(0, 6), default=0,
                        help="0 selects the starting axe; 6 selects an empty slot.")
    parser.add_argument("--approach", choices=("warp", "walk"), default="warp",
                        help="Warp directly to the stand tile, or warp nearby and walk there.")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10:
        parser.error("--repetitions must be between 1 and 10")
    proxy = ActionProxy(10783)
    game = GameTools(proxy)
    cases = [("south", (25, 8), 0), ("west", (24, 7), 1),
             ("north", (25, 6), 2), ("east", (26, 7), 3)]
    results = []
    for case_index in range(args.repetitions * len(cases)):
        repetition = case_index // len(cases) + 1
        side, (x, y), direction = cases[case_index % len(cases)]
        reset = subprocess.run([sys.executable, "-m", "project.tools.control_smoke", "--load-test-save"],
                               cwd=REPO_ROOT, text=True, capture_output=True, timeout=30)
        if reset.returncode:
            raise RuntimeError(f"Fixture reset failed for {side}: {reset.stdout[-1000:]} {reset.stderr[-1000:]}")
        save_line = next((line for line in reset.stdout.splitlines() if line.startswith("Loading separate test save:")), "")
        save_name = save_line.partition(": ")[2]
        start_x, start_y = (25, 9) if args.approach == "walk" else (x, y)
        proxy._post_message(f"warp%Beach%{start_x}%{start_y}")  # Fixture setup only; not agent-accessible.
        for _ in range(30):
            time.sleep(0.2)
            before = game.observe()
            if before["player"].get("location") == "Beach" and before["player"].get("position") == {"x": start_x, "y": start_y}:
                break
        else:
            raise RuntimeError(f"Fixture did not reach {side} starting tile.")
        if args.approach == "walk":
            reply = proxy._post_message(f"move_relative%{x-start_x}%{y-start_y}")
            time.sleep(0.5)
            before = game.observe()
            if before["player"].get("position") != {"x": x, "y": y}:
                result = {"repetition": repetition, "side": side, "position": [x, y],
                          "direction": direction, "selected_slot": args.selected_slot, "approach": "walk",
                          "save": save_name, "inconclusive": "Could not reach stand tile", "move_reply": reply,
                          "actual_position": before["player"].get("position")}
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                continue
        proxy.choose_item(args.selected_slot)
        before = game.observe()
        if before["player"].get("currentinventory", {}).get("index") != args.selected_slot:
            raise RuntimeError(f"Could not select slot {args.selected_slot} for {side}.")
        if not clam_on_ground(before) or clam_count(before):
            raise RuntimeError(f"Unexpected clam fixture for {side}; cannot compare.")
        attempts = []
        for attempt in range(1, 4):
            proxy.interact(direction)
            time.sleep(0.7)
            after = game.observe()
            picked = clam_count(after) > clam_count(before) and not clam_on_ground(after)
            attempts.append({"attempt": attempt, "inventory_clams": clam_count(after),
                             "ground_clam": clam_on_ground(after), "picked": picked,
                             "position": after["player"].get("position"),
                             "facing": after["player"].get("facingdirection")})
            if picked:
                break
        result = {"repetition": repetition, "side": side, "position": [x, y], "direction": direction,
                  "selected_slot": args.selected_slot, "approach": args.approach,
                  "game_in_foreground_at_reset": '"game_in_foreground": true' in reset.stdout,
                  "save": save_name, "picked": attempts[-1]["picked"], "attempts": attempts}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    output = REPO_ROOT / ".tools" / "clam-interaction" / f"slot-{args.selected_slot}-{args.approach}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"target": [25, 7], "llm_calls": 0, "cases": results}, indent=2), encoding="utf-8")
    print("Report:", output, flush=True)


if __name__ == "__main__":
    main()
