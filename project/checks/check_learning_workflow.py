"""Skill lifecycle, daily review controls and monitor presentation checks."""

import copy
import json
import unittest
from unittest.mock import Mock

from project.agents.hybrid import HybridAgent
from project.agents.hybrid_reflection import ReflectionHybridAgent
from project.agents.hybrid_economics import EconomicsHybridAgent
from project.checks.check_hybrid import definition
from project.checks.check_hybrid_variations import observed, plan, economic_review
from project.experiment_runner import parse_args
from project.tools.log_viewer import dashboard_data


def monitor_fixture():
    """Clearly synthetic sample used by widget/visual checks, never an experiment."""
    economics = economic_review(status="supported", evidence_ids=["e3", "e7"], net_gold_estimate=120,
                                active_minutes_estimate=30, payback_days_estimate=4)
    economics["activities"][0].update(observation="DEMO: one small batch completed; not actual experiment data.",
                                     decision="Maintain the existing patch, then use remaining time for fishing.")
    economics["activities"].append({**economics["activities"][0], "activity": "Fishing session",
                                    "status": "hypothesis", "net_gold_estimate": None,
                                    "payback_days_estimate": None,
                                    "uncertainty": "Sample too small to estimate income per hour.",
                                    "next_test": "Record another short session and its sale result."})
    return {"goal": "DEMO / Reach 2000 gold efficiently across as many days as needed",
            "model": "offline-preview", "strategy": "hybrid_economics", "phase": "thinking",
            "usage": {"prompt_tokens": 14000, "completion_tokens": 2000},
            "game": {"location": "Farm", "money": 760,
                     "time": {"year": 1, "season": "spring", "dayofmonth": 3, "time": 700}},
            "plan": {"date": [1, "spring", 3], "day_goal": "Maintain crops and earn additional cash",
                     "activities": "1. Inspect and water the planted patch.\n2. Fish while crops grow.\n3. Sell a batch before closing; reassess cash.",
                     "verification": "Observed crop watering and actual cash increase after sale.",
                     "skill_review": "water_patch passed its condition twice. collect_shell failed: target was not adjacent.",
                     "tool_opportunity": "Repair collect_shell and test at the next reachable shell."},
            "workflow": {"plan_required": False, "synthesis_enabled": True},
            "skills": {"water_patch": {"description": "Water an adjacent planted tile; select watering can first.",
                "verified": True, "revision": 1, "trials": 1, "uses": 1, "passes": 2, "failures": 0,
                "last_status": "passed", "postcondition": {"path": "farming.soils.0.watered", "op": "equals", "value": True},
                "steps": [{"tool": "choose_item", "arguments": {"slot_index": 2}},
                          {"tool": "use", "arguments": {"direction": 2}}]},
                       "collect_shell": {"description": "Collect a shell at an observed adjacent tile.", "verified": False,
                "revision": 1, "trials": 1, "uses": 0, "passes": 0, "failures": 1,
                "last_status": "failed", "last_error": "Inventory did not change."}},
            "learning": {"mode": "economic", "review_count": 3,
                         "latest_review": {"date": [1, "spring", 3], "revision": 1, "review": economics}}}


class LearningWorkflowChecks(unittest.TestCase):
    def test_failed_reuse_revokes_verification_and_repair_requires_new_trial(self):
        for kind in (HybridAgent, ReflectionHybridAgent, EconomicsHybridAgent):
            with self.subTest(kind=kind):
                agent = kind(Mock(), "offline")
                agent.define_tool(definition())
                args = {"name": "step_east", "arguments_json": '{"direction":2}'}
                before, after = observed(), observed()
                after["player"]["position"] = [1, 0]
                result = agent.run_tool(args, before, Mock(return_value=({"ok": True}, after)), True, lambda: False)
                self.assertTrue(result["verified"])
                with self.assertRaises(ValueError):
                    agent.define_tool(definition())
                result = agent.run_tool(args, after, Mock(return_value=({"ok": True}, after)), False, lambda: False)
                self.assertFalse(result["verified"])
                skill = agent.library_snapshot()["step_east"]
                self.assertEqual((skill["trials"], skill["uses"], skill["passes"], skill["failures"]), (1, 1, 1, 1))
                self.assertEqual(skill["last_status"], "failed")
                with self.assertRaises(ValueError):
                    agent.run_tool(args, before, Mock(), False, lambda: False)
                agent.define_tool(definition())
                self.assertEqual(agent.library["step_east"]["revision"], 2)
                with self.assertRaises(ValueError):
                    agent.run_tool(args, before, Mock(), False, lambda: False)
                self.assertTrue(agent.run_tool(args, before, Mock(return_value=({"ok": True}, after)), True, lambda: False)["verified"])

    def test_primitive_error_and_missing_postcondition_are_failed_not_learned(self):
        for outcome in ("api", "missing"):
            agent = HybridAgent(Mock(), "offline")
            agent.define_tool(definition())
            after = observed()
            after["player"].pop("position")
            execute = Mock(return_value=({"ok": False, "error": "blocked"}, observed()) if outcome == "api" else ({"ok": True}, after))
            result = agent.run_tool({"name": "step_east", "arguments_json": '{"direction":2}'}, observed(), execute, True, lambda: False)
            self.assertFalse(result["verified"])
            self.assertEqual(agent.library["step_east"]["failures"], 1)

    def test_budget_interruption_does_not_invent_a_failure_or_success(self):
        agent = HybridAgent(Mock(), "offline")
        agent.define_tool(definition())
        result = agent.run_tool({"name": "step_east", "arguments_json": '{"direction":2}'}, observed(),
                                Mock(return_value=({"ok": False, "error": "budget"}, None)), True, lambda: False)
        skill = agent.library["step_east"]
        self.assertFalse(result["verified"])
        self.assertEqual(skill["last_status"], "interrupted")
        self.assertEqual((skill["passes"], skill["failures"]), (0, 0))

    def test_daily_workflow_reports_new_date_before_next_decision(self):
        for kind in (ReflectionHybridAgent, EconomicsHybridAgent):
            agent = kind(Mock(), "offline")
            fields = plan()
            if kind is ReflectionHybridAgent:
                fields["daily_reflection"] = "First day: no observed history."
            if kind is EconomicsHybridAgent:
                fields["economics_json"] = json.dumps(economic_review())
                fields['daily_reflection'] = 'First day: no prior observations.'
            self.assertTrue(agent.workflow_status(observed())["plan_required"])
            agent.set_daily_plan(fields, observed())
            self.assertFalse(agent.workflow_status(observed())["plan_required"])
            self.assertTrue(agent.workflow_status(observed(2))["plan_required"])
            self.assertEqual(len(agent.daily_plans), 1)

    def test_baseline_skill_interrupts_at_day_change_without_a_plan_gate(self):
        agent = HybridAgent(Mock(), 'offline')
        agent.define_tool(definition())
        execute = Mock(return_value=({'ok': True}, observed(2)))
        result = agent.run_tool({'name': 'step_east', 'arguments_json': '{"direction":2}'},
                                observed(), execute, trial=True, completed=lambda: False)
        self.assertTrue(result['interrupted'])
        self.assertFalse(result['daily_plan_required'])
        self.assertFalse(agent.requires_daily_plan(observed(2)))
        execute.assert_called_once()

    def test_hybrid_defaults_allow_daily_reviews_and_skills_are_enabled(self):
        for strategy in ("hybrid", "hybrid_reflection", "hybrid_economics"):
            args = parse_args(["--strategy", strategy])
            self.assertEqual(args.max_completion_tokens, 2048)
            self.assertFalse(args.disable_skill_synthesis)
        self.assertEqual(parse_args([]).max_completion_tokens, 512)
        self.assertEqual(parse_args(["--strategy", "hybrid", "--max-completion-tokens", "100"]).max_completion_tokens, 100)


class DashboardChecks(unittest.TestCase):
    def test_status_distinguishes_verified_skills_from_economic_claims(self):
        source = monitor_fixture()
        view = dashboard_data(source)
        self.assertEqual([row["tag"] for row in view["skill_rows"]], ["passed", "failed"])
        self.assertEqual([row["tag"] for row in view["economy_rows"]], ["supported", "hypothesis"])
        self.assertEqual(view["economy_rows"][1]["values"][2], "unknown")
        self.assertIn("Tokens 16,000", view["metrics"])
        self.assertIn("Trials 1 | Reuses 1", view["skill_rows"][0]["detail"])
        self.assertIn("NEXT TEST", view["economy_rows"][0]["detail"])
        self.assertIn("Synthesis ON", view["skill_caption"])
        self.assertNotIn("step", view["metrics"].lower())

    def test_new_day_labels_old_plan_and_review_stale(self):
        data = monitor_fixture()
        data["game"]["time"]["dayofmonth"] = 4
        data.pop("workflow")
        view = dashboard_data(data)
        self.assertTrue(view["pending"])
        self.assertIn("NEW DAY", view["plan"])
        self.assertIn("PREVIOUS DATE", view["review"])

    def test_disabled_skills_and_non_economic_variants_have_clear_empty_states(self):
        view = dashboard_data({"skill_synthesis": False, "learning": {"mode": "free_form",
                              "latest_review": {"review": "Keep a cash reserve"}}})
        self.assertIn("Synthesis OFF", view["skill_caption"])
        self.assertIn("no structured economic ledger", view["economy_empty"])
        self.assertEqual(view["review"], "Keep a cash reserve")
        self.assertEqual(dashboard_data({})["skill_rows"], [])

    def test_widget_render_keeps_selection_during_updates(self):
        import tkinter as tk
        from project.tools.log_viewer import LiveMonitor
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        root.withdraw()
        try:
            monitor = LiveMonitor(root)
            source = monitor_fixture()
            monitor.render(source)
            monitor.skill_table.selection_set("collect_shell")
            changed = copy.deepcopy(source)
            changed["skills"]["collect_shell"]["last_error"] = "New observed error"
            monitor.render(changed)
            root.update_idletasks()
            self.assertEqual(monitor.skill_table.selection(), ("collect_shell",))
            self.assertIn("New observed error", monitor.skill_detail.get("1.0", "end"))
            self.assertEqual(len(monitor.economy_table.get_children()), 2)
            monitor.append_log("Offline sample\n")
            self.assertIn("Offline sample", monitor.log.get("1.0", "end"))
            monitor.render({})
            self.assertEqual(monitor.skill_table.get_children(), ())
        finally:
            root.destroy()
