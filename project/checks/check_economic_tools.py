"""Check money-route tool dispatch and recipe mapping without an LLM."""
import json
import unittest
from unittest.mock import Mock
from project.agents.react import GameTools, TOOL_SPECS, validate_call


class EconomicToolChecks(unittest.TestCase):
    def test_on_demand_observations_dispatch_and_validate(self):
        proxy = Mock()
        tools = GameTools(proxy)
        for name, payload in (
            ("observe_npcs", {"available": True, "scope": "all_enumerated_characters_original_mod", "npcs": []}),
            ("observe_farm", {"available": True, "farm": {"animals": [], "buildings": [], "pets": []}}),
            ("observe_location_details", {"available": True, "doors": [], "counters": [], "furniture": []}),
        ):
            with self.subTest(name=name):
                getattr(proxy, name).return_value = json.dumps(payload)
                self.assertEqual(validate_call(name, '{"reason":"inspect"}')[1], {})
                self.assertEqual(tools.execute(name, {})["data"], payload)
                getattr(proxy, name).assert_called_once_with()

    def test_known_recipes_get_existing_craft_index_without_inventing_one(self):
        proxy = Mock()
        proxy.observe_recipes.return_value = json.dumps({"available": True, "recipes": [
            {"name": "Chest", "can_craft": True}, {"name": "Unknown Mod Recipe", "can_craft": False}]})
        result = GameTools(proxy, crafting_recipes=["Wood Fence", "Chest"]).execute("observe_recipes", {})
        self.assertEqual([r["craft_index"] for r in result["data"]["recipes"]], [1, None])

    def test_filtered_recipes_and_location(self):
        proxy = Mock()
        proxy.observe_world_map.return_value = json.dumps({"available": True, "locations": [
            {"name": "Farm"}, {"name": "Town", "exits": []}]})
        proxy.observe_recipes.return_value = json.dumps({"available": True, "recipes": [
            {"name": "Chest", "can_craft": True}, {"name": "Wood Fence", "can_craft": False}]})
        game = GameTools(proxy, crafting_recipes=["Chest", "Wood Fence"])
        _, args = validate_call("observe_map_location", '{"reason":"route","location":"town"}')
        self.assertEqual(game.execute("observe_map_location", args)["data"]["locations"][0]["name"], "Town")
        _, args = validate_call("search_recipes", '{"reason":"craft","name":"ch","craftable_only":1}')
        self.assertEqual(game.execute("search_recipes", args)["data"]["recipes"][0]["craft_index"], 0)
        self.assertEqual(game.execute("search_recipes", {"name": "wood", "craftable_only": 1})["data"]["recipes"], [])
        with self.assertRaises(ValueError):
            validate_call("search_recipes", '{"reason":"x","name":3,"craftable_only":0}')

    def test_actions_are_allowlisted_with_bounded_integers(self):
        for name in ("observe_machines", "observe_recipes", "observe_quests", "carpenter", "open_quests", "quest_menu"):
            self.assertIn(name, TOOL_SPECS)
        for name, args in [("carpenter", '{"reason":"x","action":6,"x":0,"y":0}'),
                           ("quest_menu", '{"reason":"x","action":4,"index":0}'),
                           ("set_money", '{"reason":"x","amount":10000}')]:
            with self.assertRaises(ValueError):
                validate_call(name, args)

    def test_quest_menu_response_is_not_reported_as_success_on_rejection(self):
        proxy = Mock()
        proxy.quest_menu.return_value = '{"ok":false,"error":"Quest action is unavailable on this page."}'
        result = GameTools(proxy).execute("quest_menu", {"action": 1, "index": 0})
        self.assertFalse(result["ok"])

    def test_local_map_only_returns_current_location(self):
        proxy = Mock()
        proxy.observe_world_map.return_value = json.dumps({"available": True, "current_location": "Farm",
            "locations": [{"name": "Farm", "exits": [{"target": "Town"}]}, {"name": "Town", "exits": []}]})
        result = GameTools(proxy).execute("observe_local_map", {})["data"]
        self.assertEqual([entry["name"] for entry in result["locations"]], ["Farm"])
        self.assertEqual(result["scope"], "current_location_loaded_warps_and_buildings")
