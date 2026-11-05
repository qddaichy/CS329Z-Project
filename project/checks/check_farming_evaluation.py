"""Regression tests for farming observations and state-transition evaluators."""
import unittest

from project.agents.react import compact_observation
from project.evaluation.farming import FarmingEvaluator
from project.run_react import parse_args, task_settings


def crop(seed="Parsnip Seeds", seed_id="472"):
    return {"seed_name": seed, "seed_id": seed_id, "growth_phase": 0}


def soil(x, y, watered=False, plant=None, fertilizer_id="", fertilizer_name=None):
    return {"position": [x, y], "watered": watered, "crop": plant,
            "fertilizer_id": fertilizer_id, "fertilizer_name": fertilizer_name}


def observation(soils, location="Farm", day=1):
    return {"player": {"location": location, "position": [0, 0]},
            "gamestate": {"year": 1, "season": "spring", "dayofmonth": day},
            "surroundingsdata": [{"position": [0, 0]}, {"position": [1, 0]}],
            "farming": {"available": True, "location": location,
                        "scope": "current_location_all_soil", "soils": soils}}


class FarmingEvaluationChecks(unittest.TestCase):
    def test_till_counts_new_distinct_soil_not_existing_or_revisited(self):
        ev = FarmingEvaluator("till", count=2)
        ev.update(observation([soil(0, 0)]))
        result = ev.update(observation([soil(0, 0), soil(1, 0)]))
        self.assertEqual(result["quantity"], 1)
        ev.update(observation([soil(0, 0)]))
        result = ev.update(observation([soil(0, 0), soil(1, 0), soil(2, 0)]))
        self.assertEqual(result["quantity"], 2)
        self.assertTrue(result["completed"])

    def test_sow_requires_new_matching_seed_on_distinct_tiles(self):
        ev = FarmingEvaluator("sow", "Potato Seeds", 2)
        ev.update(observation([soil(0, 0, plant=crop("Potato Seeds")), soil(1, 0), soil(2, 0)]))
        result = ev.update(observation([soil(0, 0, plant=crop("Potato Seeds")),
                                        soil(1, 0, plant=crop("Parsnip Seeds")),
                                        soil(2, 0, plant=crop("potato seeds"))]))
        self.assertEqual(result["quantity"], 1)
        self.assertFalse(result["completed"])

    def test_water_requires_existing_crop_same_day_and_distinct_tile(self):
        ev = FarmingEvaluator("water", count=2)
        ev.update(observation([soil(0, 0, watered=True, plant=crop()), soil(1, 0, plant=crop())]))
        result = ev.update(observation([soil(0, 0, watered=True, plant=crop()),
                                        soil(1, 0, watered=True, plant=crop())]))
        self.assertEqual(result["quantity"], 1)
        ev.update(observation([soil(1, 0, plant=crop()), soil(2, 0, plant=crop())], day=2))
        result = ev.update(observation([soil(1, 0, watered=True, plant=crop()),
                                        soil(2, 0, watered=True, plant=crop())], day=2))
        self.assertEqual(result["quantity"], 2)

    def test_day_and_location_changes_do_not_invent_progress(self):
        ev = FarmingEvaluator("water")
        ev.update(observation([soil(1, 0, plant=crop())], day=1))
        self.assertEqual(ev.update(observation([soil(1, 0, watered=True, plant=crop())], day=2))["quantity"], 0)
        self.assertEqual(ev.update(observation([soil(1, 0, watered=True, plant=crop())], location="Town"))["quantity"], 0)
        self.assertEqual(ev.update(observation([soil(1, 0, watered=True, plant=crop())]))["quantity"], 0)

    def test_fertilizer_requires_matching_applied_item(self):
        ev = FarmingEvaluator("fertilize", "Speed-Gro")
        ev.update(observation([soil(0, 0)]))
        self.assertFalse(ev.update(observation([soil(0, 0, fertilizer_id="368", fertilizer_name="Basic Fertilizer")]))["completed"])
        self.assertTrue(ev.update(observation([soil(0, 0, fertilizer_id="465", fertilizer_name="Speed-Gro")]))["completed"])

    def test_incomplete_observations_error_instead_of_success(self):
        ev = FarmingEvaluator("till")
        for state in [observation([{"position": [0, 0]}]), {"player": {"location": "Farm"}},
                      {**observation([]), "farming": {"available": True, "location": "Town", "soils": []}}]:
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                ev.update(state)

    def test_compact_observation_limits_soil_details_to_visible_tiles(self):
        state = observation([soil(1, 0, plant=crop()), soil(5, 5, plant=crop())])
        compact = compact_observation(state)
        self.assertEqual(compact["farming"]["total_soils_in_location"], 2)
        self.assertEqual([item["position"] for item in compact["farming"]["soils"]], [[1, 0]])
        self.assertEqual(compact["gamestate"]["dayofmonth"], 1)

    def test_supported_benchmark_tasks_use_correct_evaluator_targets(self):
        for task_id, kind, target in [(3, "till", None), (4, "fertilize", "Basic Retaining Soil"),
                                      (6, "sow", "Cauliflower Seeds"), (8, "water", None)]:
            with self.subTest(task_id=task_id):
                _, ev, _ = task_settings(parse_args(["--task-name", "farming_lite", "--task-id", str(task_id)]))
                self.assertEqual((ev.kind, ev.target), (kind, target))


if __name__ == "__main__":
    unittest.main()
