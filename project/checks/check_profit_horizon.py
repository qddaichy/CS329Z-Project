import contextlib
import io
import json
import unittest
from unittest.mock import Mock

from project.agents.react import GameTools, ReActAgent, run_episode
from project.checks.check_hybrid_variations import observed
from project.checks.check_react_baseline import response
from project.evaluation.profit_horizon import ProfitHorizonEvaluator
from project.experiment_runner import parse_args, task_settings


class ProfitHorizonChecks(unittest.TestCase):
    def test_cash_target_cannot_end_horizon_and_final_settlement_counts(self):
        evaluator = ProfitHorizonEvaluator(5)
        evaluator.update(observed(5, 500))
        self.assertFalse(evaluator.update(observed(7, 5000))["completed"])
        self.assertFalse(evaluator.update(observed(9, 4000))["completed"])
        result = evaluator.update(observed(10, 5600))
        self.assertTrue(result["completed"])
        self.assertEqual(result["net_gain"], 5100)
        self.assertEqual(result["remaining_game_days"], 0)
        self.assertIn("terminal_inventory", result)
        self.assertEqual(evaluator.update(observed(11, 9999))["current_balance"], 5600)

    def test_calendar_rollover_and_backwards_date(self):
        evaluator = ProfitHorizonEvaluator(5)
        start = observed(27)
        start["gamestate"]["season"] = "winter"
        evaluator.update(start)
        end = observed(4, 300)
        end["gamestate"]["year"] = 2
        result = evaluator.update(end)
        self.assertTrue(result["completed"])
        self.assertEqual(result["net_gain"], -200)
        evaluator = ProfitHorizonEvaluator(5)
        evaluator.update(observed(5))
        with self.assertRaises(RuntimeError):
            evaluator.update(observed(4))

    def test_cli_goal_and_invalid_combinations(self):
        goal, evaluator, metadata = task_settings(parse_args(["--profit-days", "5"]))
        self.assertIsInstance(evaluator, ProfitHorizonEvaluator)
        self.assertIn("Maximize final cash", goal)
        self.assertEqual(metadata["required_game_days"], 5)
        for args in (["--profit-days", "0"], ["--profit-days", "5", "--min-days", "1"],
                     ["--profit-days", "5", "--money-balance", "2000"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(args)

    def test_early_finish_is_rejected_and_deadline_stops_before_another_action(self):
        proxy, client = Mock(), Mock()
        proxy.turn.return_value = None
        proxy.observe_state.side_effect = [json.dumps(s) for s in (observed(), observed(), observed(6, 700))]
        client.chat.completions.create.side_effect = [response(("finish", {"reason": "Enough cash"})),
                                                    response(("turn", {"reason": "Continue", "direction": 1}))]
        result = run_episode(ReActAgent(client, "offline"), GameTools(proxy), "test", ProfitHorizonEvaluator(5),
                             max_steps=5, settle_seconds=0)
        self.assertTrue(result["success"])
        self.assertEqual(result["llm_calls"], 2)
        self.assertEqual(result["invalid_decisions"], 1)
        self.assertEqual(result["evaluation"]["net_gain"], 200)
