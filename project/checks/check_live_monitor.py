"""Check that the live overview remains readable without duplicating steps."""
import unittest

from project.experiment_runner import parse_args, task_settings
from project.agents.high_level import PLANNER_PROMPT
from project.agents.hybrid import HybridAgent
from project.agents.hybrid_reflection import ReflectionHybridAgent
from project.agents.hybrid_economics import EconomicsHybridAgent
from project.agents.game_mechanics import GAME_MECHANICS
from project.agents.react import SYSTEM_PROMPT
from project.tools.log_viewer import overview_sections
from unittest.mock import Mock


class LiveMonitorChecks(unittest.TestCase):
    def test_overview_shows_plan_and_skill_without_current_step(self):
        data = {"goal": "Earn 2000 gold", "step": 321,
                "decision": {"action": "wait", "reason": "CURRENT_DECISION"},
                "game": {"location": "Beach", "money": 500,
                         "time": {"season": "spring", "dayofmonth": 1, "time": 600}},
                "plan": {"day_goal": "Gather fish", "verification": "Inventory increases"},
                "skills": {"fish": {"verified": True, "description": "Cast and wait"}}}
        rendered = "\n".join(text for _, text in overview_sections(data))
        for expected in ("Earn 2000 gold", "Gather fish", "fish  ·  verified", "500 gold"):
            self.assertIn(expected, rendered)
        self.assertNotIn("321", rendered)
        self.assertNotIn("CURRENT_DECISION", rendered)

    def test_money_goals_include_efficiency(self):
        for flag in ("--money-balance", "--money-net-gain"):
            goal, _, _ = task_settings(parse_args([flag, "2000"]))
            self.assertIn("efficiently", goal)
            self.assertIn("minimizing time", goal)

    def test_income_opportunities_are_shared_across_strategies(self):
        prompts = (SYSTEM_PROMPT, PLANNER_PROMPT, HybridAgent(Mock(), "offline").system_prompt())
        for prompt in prompts:
            for opportunity in ("fishing", "forage", "crops", "animals", "mining", "machines", "quests"):
                with self.subTest(opportunity=opportunity):
                    self.assertIn(opportunity, prompt)

    def test_all_agents_receive_identical_basic_mechanics_without_economic_numbers(self):
        prompts = [SYSTEM_PROMPT, PLANNER_PROMPT] + [kind(Mock(), "offline").system_prompt()
                    for kind in (HybridAgent, ReflectionHybridAgent, EconomicsHybridAgent)]
        for prompt in prompts:
            self.assertEqual(prompt.count(GAME_MECHANICS), 1)
        for term in ("Silo", "available", "not directly into the backpack", "watering", "Hay",
                     "shipping bin", "overnight settlement", "prices, yields", "observations and experience"):
            self.assertIn(term, GAME_MECHANICS)
        for term in ('till additional tiles', 'tools, equipment and upgrades', 'chests',
                     'remaining in-game days', 'cash actually realized by the deadline'):
            self.assertIn(term, GAME_MECHANICS)
        self.assertNotRegex(GAME_MECHANICS, r"\b\d+(?:\.\d+)?\s*(?:g|gold|%|days?)\b")


if __name__ == "__main__":
    unittest.main()
