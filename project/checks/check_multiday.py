import unittest

from project.agents.react import MoneyEvaluator
from project.evaluation.multiday import MultiDayEvaluator
from project.experiment_runner import parse_args, task_settings


def snapshot(year, season, day, balance):
    return {"gamestate": {"year": year, "season": season, "dayofmonth": day},
            "player": {"money": balance}}


class MultiDayChecks(unittest.TestCase):
    def test_both_goal_and_day_transition_are_required(self):
        evaluator = MultiDayEvaluator(MoneyEvaluator("balance", 2000), 1)
        self.assertFalse(evaluator.update(snapshot(1, "spring", 28, 500))["completed"])
        self.assertFalse(evaluator.update(snapshot(1, "spring", 28, 2000))["completed"])
        result = evaluator.update(snapshot(1, "summer", 1, 2000))
        self.assertTrue(result["completed"])
        self.assertEqual(result["elapsed_game_days"], 1)

    def test_day_alone_is_insufficient_and_year_transition_works(self):
        evaluator = MultiDayEvaluator(MoneyEvaluator("balance", 2000), 1)
        evaluator.update(snapshot(1, "winter", 28, 500))
        self.assertFalse(evaluator.update(snapshot(2, "spring", 1, 1000))["completed"])
        self.assertTrue(evaluator.update(snapshot(2, "spring", 1, 2000))["completed"])

    def test_cli_describes_and_enforces_both(self):
        goal, evaluator, _ = task_settings(parse_args(["--money-balance", "2000", "--min-days", "1"]))
        self.assertIn("advance at least 1", goal)
        self.assertIsInstance(evaluator, MultiDayEvaluator)

    def test_money_goal_does_not_require_next_day_by_default(self):
        goal, evaluator, _ = task_settings(parse_args(["--money-balance", "2000"]))
        self.assertNotIn("advance at least", goal)
        self.assertNotIsInstance(evaluator, MultiDayEvaluator)
        self.assertFalse(evaluator.update(snapshot(1, "spring", 5, 500))["completed"])
        self.assertTrue(evaluator.update(snapshot(1, "spring", 5, 2000))["completed"])

