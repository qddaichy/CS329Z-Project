"""Money goals must use observed balances and distinguish net gain from balance."""
import unittest
import json
from unittest.mock import MagicMock
from project.agents.react import GameTools, MoneyEvaluator, TOOL_SPECS, validate_call
from project.run_react import parse_args, task_settings


class MoneyChecks(unittest.TestCase):
    def test_balance_and_net_gain_have_distinct_semantics(self):
        balance = MoneyEvaluator("balance", 10000)
        gain = MoneyEvaluator("net_gain", 10000)
        self.assertTrue(balance.update({"player": {"money": 10000}})["completed"])
        self.assertFalse(gain.update({"player": {"money": 10000}})["completed"])
        self.assertEqual(gain.update({"player": {"money": 9500}})["quantity"], -500)
        self.assertTrue(gain.update({"player": {"money": 20000}})["completed"])

    def test_missing_balance_fails_closed(self):
        for money in (None, True, -1, "10000"):
            with self.subTest(money=money), self.assertRaises(RuntimeError):
                MoneyEvaluator("balance", 10000).update({"player": {"money": money}})

    def test_cli_and_policy(self):
        _, evaluator, _ = task_settings(parse_args(["--money-net-gain", "10000"]))
        self.assertEqual((evaluator.mode, evaluator.amount), ("net_gain", 10000))
        self.assertIn("sell_current_item", TOOL_SPECS)
        self.assertIn("observe_economy", TOOL_SPECS)
        for name in ("set_money", "load_game_record", "open_shop", "execute_code"):
            with self.assertRaises(ValueError):
                validate_call(name, '{"reason":"x"}')

    def test_shipping_requires_facing_the_adjacent_bin(self):
        proxy = MagicMock()
        proxy.observe_state.return_value = json.dumps({"Player": {"location": "Farm",
            "position": {"x": 70, "y": 15}, "currentinventory": {"index": 5}},
            "SurroundingsData": []})
        proxy.observe_economy.return_value = json.dumps({"shipping_bin_position": [71, 14],
            "inventory": [{"slot": 5, "shippable": True}]})
        with self.assertRaises(ValueError):
            GameTools(proxy).execute("interact", {"direction": 0})
        proxy.interact.assert_not_called()
        state = json.loads(proxy.observe_state.return_value)
        state["Player"]["position"] = {"x": 72, "y": 15}
        proxy.observe_state.return_value = json.dumps(state)
        GameTools(proxy).execute("interact", {"direction": 0})
        proxy.interact.assert_called_once_with(direction=0)
