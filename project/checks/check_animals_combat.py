import unittest
from unittest.mock import Mock, patch
from project.agents.react import GameTools, validate_call
from project.evaluation.animals_combat import PetEvaluator, CombatEvaluator
from project.run_react import parse_args, task_settings


def pet_state(day, animals, location="Coop"):
    return {"gamestate": {"year": 1, "season": "spring", "dayofmonth": day},
            "entities": {"available": True, "location": location, "animals": animals}}


class AnimalsCombatChecks(unittest.TestCase):
    def test_pet_counts_only_observed_same_day_transitions_once(self):
        ev = PetEvaluator(2)
        before = [{"id": "a", "petted_today": False}, {"id": "b", "petted_today": True}]
        ev.update(pet_state(1, before))
        result = ev.update(pet_state(1, [{"id": "a", "petted_today": True}, {"id": "b", "petted_today": True}]))
        self.assertEqual(result["quantity"], 1)
        ev.update(pet_state(2, [{"id": "a", "petted_today": False}, {"id": "c", "petted_today": False}]))
        result = ev.update(pet_state(2, [{"id": "a", "petted_today": True}, {"id": "c", "petted_today": True}]))
        self.assertTrue(result["completed"])

    def test_kill_counts_increment_not_disappearance(self):
        ev = CombatEvaluator("Green Slime", 2)
        make = lambda n: {"combat": {"available": True, "monsters_killed": [{"name": "Green Slime", "count": n}]}}
        self.assertEqual(ev.update(make(5))["quantity"], 0)
        self.assertFalse(ev.update(make(6))["completed"])
        self.assertTrue(ev.update(make(7))["completed"])
        with self.assertRaises(RuntimeError): ev.update({})

    def test_fishing_and_animal_controls_fail_closed_and_bound_inputs(self):
        for name, args in [("fish_input", '{"reason":"x","pressed":2,"duration_ms":100}'),
                           ("fish_input", '{"reason":"x","pressed":1,"duration_ms":1001}'),
                           ("pet_animal", '{"reason":"x","direction":4}')]:
            with self.assertRaises(ValueError): validate_call(name, args)
        proxy=Mock()
        proxy.fish_hook.return_value = '{"ok":false,"error":"No bite"}'
        self.assertFalse(GameTools(proxy).execute("fish_hook", {})["ok"])
        proxy.fish_input.return_value = '{"ok":false,"error":"No minigame"}'
        with patch("project.agents.react.time.sleep") as sleep:
            self.assertFalse(GameTools(proxy).execute("fish_input", {"pressed": 1, "duration_ms": 100})["ok"])
            sleep.assert_not_called()

    def test_cli_and_benchmark(self):
        _, ev, _ = task_settings(parse_args(["--task-name", "farming_lite", "--task-id", "11"]))
        self.assertIsInstance(ev, PetEvaluator)
        _, ev, _ = task_settings(parse_args(["--defeat-monster", "Green Slime", "--quantity", "2"]))
        self.assertIsInstance(ev, CombatEvaluator)
