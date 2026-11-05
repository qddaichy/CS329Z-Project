"""Check StarDojo game control directly, without importing or calling an LLM."""

import argparse
from collections import deque
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time

from project.paths import REPO_ROOT, DATA_ROOT, ENV_FILE


def grass_tiles(tiles):
    return {point for point, tile in tiles.items()
            if str(tile.get("terrain_at_tile", "")).rsplit(".", 1)[-1] == "Grass"}


def grass_approaches(tiles, start, excluded):
    """Find nearby grass with a reachable adjacent tile in the observed area."""
    distances = {start: 0}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for point in [(x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)]:
            tile = tiles.get(point)
            if point in distances or not tile:
                continue
            terrain = str(tile.get("terrain_at_tile", "")).rsplit(".", 1)[-1]
            # Building bounding boxes include walkable steps, and "placeable"
            # describes item placement rather than walking. The mod's pathfinder
            # checks the actual route when we request a move.
            blocked = any(tile.get(key) for key in
                          ["object_at_tile", "crop_at_tile",
                           "furniture_at_tile", "exit_info", "npc_info"])
            if blocked or terrain not in ("", "Grass"):
                continue
            if "water: true" in str(tile.get("tile_properties", "")).casefold():
                continue
            distances[point] = distances[(x, y)] + 1
            queue.append(point)

    candidates = []
    for target in sorted(grass_tiles(tiles)):
        x, y = target
        for stand, facing in [((x, y + 1), 0), ((x - 1, y), 1),
                              ((x, y - 1), 2), ((x + 1, y), 3)]:
            if stand in distances and (target, stand) not in excluded:
                candidates.append((distances[stand], target, stand, facing))
    return sorted(candidates)


def main():
    if Path(sys.prefix).name.casefold() != "cs329z_project":
        raise SystemExit("Activate the cs329z_project Conda environment first.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--load-test-save", action="store_true",
                        help="Create and load a separate copy of the benchmark save.")
    parser.add_argument("--move", action="store_true",
                        help="Test turning and moving one tile, then returning.")
    parser.add_argument("--cut-grass", type=int, default=0, metavar="COUNT",
                        help="Equip the scythe and cut at least COUNT nearby grass tiles.")
    args = parser.parse_args()
    if args.cut_grass < 0:
        parser.error("--cut-grass must be zero or a positive number.")
    project = REPO_ROOT
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
    from project.game.actions import ActionProxy, convert_to_case_insensitive_dict

    with socket.create_connection(("127.0.0.1", args.port), timeout=5):
        pass
    proxy = ActionProxy(args.port)
    report = {"port": args.port, "llm_calls": 0, "observation_mode": "state_only"}
    output = project / ".tools" / "control-smoke" / "result.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    def observe(radius=3):
        started = time.perf_counter()
        raw = proxy.observe_state(radius)
        if not raw:
            raise RuntimeError("The mod returned no observation.")
        try:
            obs = convert_to_case_insensitive_dict(json.loads(raw))
        except (ValueError, TypeError, AttributeError) as error:
            raise RuntimeError("State-only observations are unavailable. Install StateOnlyMod "
                               "and restart SMAPI.") from error
        if "player" not in obs or "surroundingsdata" not in obs:
            raise RuntimeError("The state-only response is missing player or tile data.")
        if obs.get("screenshot"):
            raise RuntimeError("The state-only response unexpectedly contains image data.")
        metrics = report.setdefault("observation_metrics", {"calls": 0, "bytes": 0, "seconds": 0})
        metrics["calls"] += 1
        metrics["bytes"] += len(raw.encode("utf-8"))
        metrics["seconds"] += time.perf_counter() - started
        return obs

    def player_summary(obs):
        player = obs["player"]
        return {key: player.get(key) for key in
                ["name", "location", "position", "facingdirection", "currentinventory", "money"]}

    def position(obs):
        point = obs["player"]["position"]
        return (point["x"], point["y"]) if isinstance(point, dict) else tuple(point)

    def focus_game():
        # Some game configurations stop movement while the window is inactive.
        # Restore only the game window, rather than the SMAPI console.
        if sys.platform != "win32":
            return
        import win32con
        import win32gui
        handles = []
        win32gui.EnumWindows(
            lambda handle, _: handles.append(handle)
            if win32gui.GetWindowText(handle).startswith("Stardew Valley ") else None,
            None,
        )
        if handles:
            win32gui.ShowWindow(handles[0], win32con.SW_RESTORE)
            try:
                win32gui.SetForegroundWindow(handles[0])
            except Exception:
                pass
            report["game_in_foreground"] = win32gui.GetForegroundWindow() == handles[0]

    def cut_grass(count):
        radius = 12
        obs = observe(radius)
        location = obs["player"]["location"]
        inventory = obs["player"]["inventory"]
        scythe_slot = next((index for index, item in enumerate(inventory)
                            if "scythe" in str(item.get("name", "")).casefold()), None)
        if scythe_slot is None:
            raise RuntimeError("No scythe found in the inventory; no items were added.")
        original_slot = obs["player"]["currentinventory"]["index"]
        original_facing = obs["player"]["facingdirection"]
        result = {"requested": count, "cut": 0, "swings": 0,
                  "scythe_slot": scythe_slot, "cuts": [], "navigation": []}
        report["grass"] = result
        removed = set()
        excluded = set()
        target_swings = {}
        try:
            focus_game()
            proxy.choose_item(scythe_slot)
            obs = observe(radius)
            if "scythe" not in str(obs["player"]["currentinventory"]["currentitem"]).casefold():
                raise RuntimeError("The scythe was not successfully selected.")
            while len(removed) < count and result["swings"] < count * 12:
                if obs["player"]["location"] != location:
                    raise RuntimeError("The player left the starting location; stopping.")
                tiles = {tuple(tile["position"]): tile for tile in obs["surroundingsdata"]}
                plans = grass_approaches(tiles, position(obs), excluded)
                if not plans:
                    if radius < 36:
                        radius += 12
                        obs = observe(radius)
                        continue
                    raise RuntimeError("No more reachable grass in the observed area.")
                _, target, stand, facing = plans[0]
                if position(obs) != stand:
                    x, y = position(obs)
                    focus_game()
                    reply = proxy._post_message(f"move_relative%{stand[0] - x}%{stand[1] - y}")
                    obs = observe(radius)
                    result["navigation"].append({"target": list(stand), "reply": reply,
                                                 "reached": position(obs) == stand})
                    if position(obs) != stand:
                        excluded.add((target, stand))
                        if len(result["navigation"]) >= 15:
                            raise RuntimeError("Too many unsuccessful navigation attempts.")
                        continue
                before = {tuple(tile["position"]): tile for tile in obs["surroundingsdata"]}
                focus_game()
                proxy.use(facing)
                result["swings"] += 1
                target_swings[target] = target_swings.get(target, 0) + 1
                time.sleep(0.7)
                obs = observe(radius)
                after = {tuple(tile["position"]): tile for tile in obs["surroundingsdata"]}
                # Compare only tiles observed both before and after the swing.
                cut = (grass_tiles(before) - grass_tiles(after)) & after.keys()
                cut -= removed
                removed.update(cut)
                result["cut"] = len(removed)
                result["cuts"].append({"target": list(target), "standing_at": list(stand),
                                        "facing": facing, "removed_tiles": [list(p) for p in sorted(cut)]})
                print(f"Scythe swing {result['swings']}: cut {len(cut)} grass tiles; "
                      f"total {len(removed)}/{count}.", flush=True)
                if target_swings[target] >= 8 and target in grass_tiles(after):
                    excluded.add((target, stand))
            result["verified"] = len(removed) >= count
            result["removed_tiles"] = [list(point) for point in sorted(removed)]
            if not result["verified"]:
                raise RuntimeError(f"Only cut {len(removed)} of the requested {count} grass tiles.")
        finally:
            focus_game()
            proxy.choose_item(original_slot)
            proxy.turn(original_facing)
        report["after"] = player_summary(observe())

    try:
        if args.load_test_save:
            # A previous save's menu can survive a direct record load and keep
            # references to the old save. Close it before preparing the fixture.
            proxy.resume_game()
            raw_menu = proxy.observe_menu()
            if raw_menu:
                menu = json.loads(raw_menu)
                if menu.get("available") and menu.get("is_open"):
                    proxy.exit_menu()
                    time.sleep(.5)
                    after_menu = json.loads(proxy.observe_menu())
                    if after_menu.get("is_open"):
                        raise RuntimeError("Close the current menu before loading the test save.")
            source = DATA_ROOT / "saves/save_new/StarDojo_406041616"
            name = "StarDojo_406041616_CodeSmoke_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            destination = Path(os.environ["APPDATA"]) / "StardewValley/Saves" / name
            shutil.copytree(source, destination)  # Fails rather than replacing a save.
            (destination / source.name).rename(destination / name)
            report["test_save"] = str(destination)
            print("Loading separate test save:", name, flush=True)
            report["load_reply"] = proxy._post_message("load_game_record%" + name)
            time.sleep(3)
            focus_game()
            report["starting_money"] = 500
            proxy._post_message(f"set_money%{report['starting_money']}")

        obs = observe()
        if args.load_test_save and obs["player"]["money"] != report["starting_money"]:
            raise RuntimeError("The test save's starting balance could not be set to 500 gold.")
        report["before"] = player_summary(obs)
        screenshot = obs.get("screenshot")
        report["screenshot_bytes_present"] = bool(screenshot)
        print("Observed:", json.dumps(report["before"]), flush=True)

        if args.move:
            focus_game()
            original_position = position(obs)
            original_facing = obs["player"]["facingdirection"]
            target_facing = (original_facing + 1) % 4
            proxy.turn(target_facing)
            obs = observe()
            report["turn_verified"] = obs["player"]["facingdirection"] == target_facing
            if not report["turn_verified"]:
                raise RuntimeError("Turning did not produce the expected observation.")

            report["movement_attempts"] = []
            for direction, dx, dy, reverse in [(2, 1, 0, 4), (4, -1, 0, 2),
                                               (3, 0, 1, 1), (1, 0, -1, 3)]:
                focus_game()
                reply = proxy._post_message(f"move_step%{direction}")
                after = observe()
                current = position(after)
                report["movement_attempts"].append({"direction": direction, "reply": reply,
                                                    "position": list(current)})
                if current != original_position:
                    report["after_move"] = player_summary(after)
                    report["movement_verified"] = current == (original_position[0] + dx,
                                                               original_position[1] + dy)
                    focus_game()
                    proxy._post_message(f"move_step%{reverse}")
                    restored = observe()
                    report["returned_to_start"] = position(restored) == original_position
                    break
            proxy.turn(original_facing)
            report["after"] = player_summary(observe())
            if not report.get("movement_verified"):
                raise RuntimeError("No one-tile movement could be verified.")
            if not report.get("returned_to_start"):
                raise RuntimeError("Movement worked, but returning to the start failed.")

        if args.cut_grass:
            cut_grass(args.cut_grass)

        report["passed"] = True
    except Exception as error:
        report["passed"] = False
        report["error"] = str(error)
        raise
    finally:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        print("Report:", output, flush=True)


if __name__ == "__main__":
    main()
