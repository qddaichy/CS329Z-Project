"""Read the current game menu or world-location graph without calling a model."""
import argparse
import json

from project.agents.react import GameTools
from project.game.actions import ActionProxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--menu", action="store_true", help="Read current menu details.")
    parser.add_argument("--world", action="store_true", help="Read loaded locations and warp connections.")
    parser.add_argument("--crops", action="store_true", help="Read soil and crop state in the current location.")
    parser.add_argument("--economy", action="store_true", help="Read inventory and pending shipments.")
    parser.add_argument("--entities", action="store_true", help="Read nearby animals and monsters.")
    parser.add_argument("--fishing", action="store_true", help="Read visible fishing state.")
    parser.add_argument("--machines", action="store_true", help="Read nearby machines.")
    parser.add_argument("--recipes", action="store_true", help="Read known crafting recipes.")
    parser.add_argument("--quests", action="store_true", help="Read accepted quests and progress.")
    args = parser.parse_args()
    if not (args.menu or args.world or args.crops or args.economy or args.entities or args.fishing or args.machines or args.recipes or args.quests):
        parser.error("Select at least one observation option.")
    from project.game.actions import _crafting_recipes
    game = GameTools(ActionProxy(args.port), crafting_recipes=_crafting_recipes["content"].keys())
    data = {}
    for selected, name in ((args.menu, "observe_menu"), (args.world, "observe_world_map"),
                           (args.crops, "observe_crops"), (args.economy, "observe_economy"),
                           (args.entities, "observe_entities"), (args.fishing, "observe_fishing"),
                           (args.machines, "observe_machines"), (args.recipes, "observe_recipes"),
                           (args.quests, "observe_quests")):
        if selected:
            data[name] = game.execute(name, {})["data"]
    print(json.dumps(data, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
