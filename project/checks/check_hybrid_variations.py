"""Offline experiment controls, daily evidence and reflection integration."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from project.agents.daily_experience import DailyExperience
from project.agents.hybrid import HybridAgent
from project.agents.hybrid_reflection import ReflectionHybridAgent
from project.agents.hybrid_economics import EconomicsHybridAgent
from project.agents.react import GameTools, run_episode
from project.checks.check_hybrid import definition
from project.checks.check_react_baseline import response, state
from project.experiment_runner import build_agent, parse_args
from project.tools.log_viewer import overview_sections


def observed(day=1, money=500, grass=()):
    return {**state(grass=grass), "player": {**state()["player"], "money": money, "stamina": 200},
            "gamestate": {"year": 1, "season": "spring", "dayofmonth": day, "time": 600}}


def plan(**extra):
    if 'economics_json' in extra and 'daily_reflection' not in extra:
        extra['daily_reflection'] = 'Results: none observed. Problems: none yet. Lessons: unknown. Next: inspect.'
    return {"day_goal": "Earn efficiently", "verification": "Observe balance",
            "activities": "Inspect actual opportunities", "tool_opportunity": "disabled",
            "skill_review": "disabled", **extra}


def economic_review(**overrides):
    item = {"activity": "Small crop batch", "status": "hypothesis", "evidence_ids": [],
            "observation": "No completed production cycle observed; provisional one-batch forecast", "net_gold_estimate": 20,
            "active_minutes_estimate": 30, "payback_days_estimate": None,
            "uncertainty": "Unknown costs and returns", "next_test": "Check observed prices first",
            "decision": "Avoid committing all cash without evidence", **overrides}
    return {"activities": [item], "allocation_plan": "Investigate before allocating capital"}


class DailyEvidenceChecks(unittest.TestCase):
    def test_only_reflective_variants_require_skill_review(self):
        agents = [kind(Mock(), 'offline') for kind in (HybridAgent, ReflectionHybridAgent, EconomicsHybridAgent)]
        self.assertNotIn('set_daily_plan', agents[0].extra_tool_specs())
        fields = [set(), *[set(agent.extra_tool_specs()['set_daily_plan'][1]) for agent in agents[1:]]]
        self.assertIn('skill_review', fields[1])
        self.assertIn('skill_review', fields[2])
        self.assertNotIn('In skill_review, review yesterday', agents[0].system_prompt())
        for agent in agents[1:]:
            self.assertIn('In skill_review, review yesterday', agent.system_prompt())
        self.assertTrue(all('define_code_tool' in agent.extra_tool_specs() for agent in agents))

    def test_cross_day_settlement_keeps_both_dates_and_cash_without_claiming_profit(self):
        ledger = DailyExperience()
        ledger.record({"type": "initial_observation", "state": observed()})
        ledger.record({"type": "step", "step": 1, "action": "wait", "arguments": {"duration_ms": 50},
                       "result": {"ok": True}, "state": observed(2, 620)})
        data = ledger.export()
        self.assertEqual(len(data), 2)
        event = data[0]["events"][0]
        self.assertTrue(event["crossed_day"])
        self.assertEqual(event["observed_cash_delta"], 120)
        self.assertEqual(event["before"]["date"], [1, "spring", 1])
        self.assertEqual(event["after"]["date"], [1, "spring", 2])
        self.assertNotIn("profit", event)
        self.assertEqual(data[1]["events"], [])

    def test_primitive_evidence_is_not_double_counted_and_prompt_is_bounded(self):
        ledger = DailyExperience()
        s = observed()
        ledger.record({"type": "initial_observation", "state": s})
        for step in range(60):
            event = {"type": "tool_primitive", "step": step, "action": "turn", "arguments": {"direction": 1},
                     "result": {"ok": True, "details": "x" * 3000}, "state": s}
            ledger.record(event)
            ledger.record({**event, "type": "step", "action": "run_tool"})
        self.assertEqual(len(ledger.evidence_ids), 60)
        context = ledger.context(review=True)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)), 18000)
        self.assertGreater(context["days"][0]["omitted_events"], 0)
        self.assertEqual(len(ledger.export()[0]["events"]), 60)
        # Context truncation must not mutate the archive.
        context["days"][0]["start"]["money"] = -1
        self.assertEqual(ledger.export()[0]["start"]["money"], 500)

    def test_missing_dates_do_not_invent_days(self):
        ledger = DailyExperience()
        ledger.record({"type": "initial_observation", "state": state()})
        self.assertEqual(ledger.export(), [])

    def test_daily_evidence_does_not_reveal_unobserved_crop_tiles(self):
        ledger = DailyExperience()
        s = observed()
        s["farming"] = {"soils": [{"position": [0, 0], "watered": True},
                                    {"position": [99, 99], "crop": "hidden"}]}
        ledger.record({"type": "initial_observation", "state": s})
        self.assertEqual(ledger.context()["days"][0]["start"]["visible_soils"],
                         [{"position": [0, 0], "watered": True}])


class VariationChecks(unittest.TestCase):
    def test_cli_and_schemas_cover_all_variants_and_synthesis_control(self):
        for strategy, cls in (("hybrid", HybridAgent), ("hybrid_reflection", ReflectionHybridAgent),
                              ("hybrid_economics", EconomicsHybridAgent)):
            for disabled in (False, True):
                args = parse_args(["--strategy", strategy] + (["--disable-skill-synthesis"] if disabled else []))
                agent = build_agent(args, Mock(), "offline")
                self.assertIsInstance(agent, cls)
                schemas = agent.available_tool_schemas()
                names = {s["function"]["name"] for s in schemas}
                self.assertEqual("define_tool" in names, not disabled)
                self.assertEqual('set_daily_plan' in names, cls is not HybridAgent)
                for schema in schemas:
                    params = schema["function"]["parameters"]
                    self.assertEqual(set(params["required"]), set(params["properties"]))
                    self.assertFalse(params["additionalProperties"])
                if disabled:
                    with self.assertRaises(ValueError):
                        agent.validate_tool_call("define_tool", json.dumps({"reason": "x", **definition()}))
                    with self.assertRaises(ValueError):
                        agent.define_tool(definition())
                    with self.assertRaises(ValueError):
                        agent.run_tool({}, {}, Mock(), False, lambda: False)

    def test_reviews_are_required_and_invalid_payloads_leave_memory_unchanged(self):
        for cls, field, value in ((ReflectionHybridAgent, "daily_reflection", ""),
                                  (EconomicsHybridAgent, "economics_json", "{}")):
            agent = cls(Mock(), "offline")
            with self.assertRaises(ValueError):
                agent.validate_tool_call("set_daily_plan", json.dumps({"reason": "x", **plan()}))
            with self.assertRaises(ValueError):
                agent.set_daily_plan(plan(**{field: value}), observed())
            self.assertEqual(agent.daily_plans, [])
            self.assertEqual(agent.reviews, [])

    def test_economic_claims_need_real_evidence_and_finite_estimates(self):
        agent = EconomicsHybridAgent(Mock(), "offline")
        invalid = [{"evidence_ids": ["e999"]}, {"status": "supported"},
                   {"net_gold_estimate": True}, {"net_gold_estimate": float("nan")},
                   {"payback_days_estimate": -1}, {"active_minutes_estimate": "3"},
                   {"status": []}, {"decision": ""}]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ValueError):
                agent.validate_review(json.dumps(economic_review(**override)))
        agent.record_event({"type": "initial_observation", "state": observed()})
        agent.record_event({"type": "step", "action": "wait", "result": {"ok": True}, "state": observed()})
        accepted = economic_review(status="supported", evidence_ids=["e1"], net_gold_estimate=-20)
        self.assertEqual(agent.validate_review(json.dumps(accepted)), accepted)
        # Validation checks referential integrity, not causal truth; it cannot promote tools.
        self.assertEqual(agent.library_snapshot(), {})

    def test_memory_survives_rolling_history_and_preserves_revisions(self):
        client = Mock()
        client.chat.completions.create.return_value = response(("finish", {"reason": "Stop"}))
        for cls, field, review in ((ReflectionHybridAgent, "daily_reflection", "Avoid repeated empty trips"),
                                  (EconomicsHybridAgent, "economics_json", json.dumps(economic_review()))):
            agent = cls(client, "offline", history_turns=1)
            agent.set_daily_plan(plan(**{field: review}), observed())
            agent.set_daily_plan(plan(**{field: review}), observed())
            self.assertEqual([r["revision"] for r in agent.reviews], [1, 2])
            agent.decide("Earn", observed(2), None, [], [])
            self.assertTrue(agent.daily_plan_required)
            self.assertEqual([s["function"]["name"] for s in agent.available_tool_schemas()], ["set_daily_plan"])
            with self.assertRaises(ValueError):
                agent.validate_tool_call("wait", '{"reason":"skip review","duration_ms":50}')
            content = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
            self.assertIn("Latest retained review", content)
            self.assertIn("Avoid repeated" if field == "daily_reflection" else "Small crop batch", content)
            exported = agent.learning_snapshot()
            exported["reviews"].clear()
            self.assertEqual(len(agent.reviews), 2)
            self.assertEqual(cls(client, "offline").learning_snapshot()["reviews"], [])

    def test_cross_day_episode_counts_review_tokens_and_keeps_planning_paused(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            response(("set_daily_plan", {"reason": "First day", **plan(daily_reflection="No previous day observed")})),
            response(("wait", {"reason": "Wait", "duration_ms": 50})),
            response(("set_daily_plan", {"reason": "New day", **plan(daily_reflection="Cash settled overnight; attribution uncertain")})),
            response(("finish", {"reason": "Stop offline test"}))]
        proxy = Mock()
        proxy.observe_state.side_effect = [json.dumps(s) for s in
                                          (observed(), observed(), observed(2, 620), observed(2, 620))]
        agent = ReflectionHybridAgent(client, "offline", skill_synthesis=False)
        events = []
        with patch("project.agents.react.time.sleep"):
            result = run_episode(agent, GameTools(proxy), "test", max_steps=4, settle_seconds=0, emit=events.append)
        self.assertEqual(result["strategy"], "hybrid_reflection_api_only")
        self.assertEqual(result["stop_reason"], "agent_finished")
        self.assertEqual(result["daily_plan_calls"], 2)
        self.assertEqual(result["primitive_calls"], 1)
        self.assertEqual(result["llm_calls"], 4)
        self.assertEqual(result["prompt_tokens"], 40)
        self.assertEqual(result["completion_tokens"], 20)
        self.assertEqual(len(result["learning"]["reviews"]), 2)
        self.assertEqual(proxy.resume_game.call_count, 2)  # wait and final cleanup only
        self.assertEqual([e["evidence_id"] for e in events if "evidence_id" in e], ["e1"])
        payload = json.loads(client.chat.completions.create.call_args_list[2].kwargs["messages"][1]["content"])
        self.assertIn('"id":"e1"', payload["goal"])

    def test_live_overview_shows_economic_interpretation_without_current_step(self):
        rendered = "\n".join(text for _, text in overview_sections({"learning": {
            "mode": "economic", "latest_review": {"review": economic_review()}}}))
        self.assertIn("DAILY REVIEW", rendered)
        self.assertIn("Small crop batch [hypothesis]", rendered)
        self.assertIn("Unknown costs and returns", rendered)
        self.assertNotIn("CURRENT STEP", rendered)

    def test_both_entry_points_write_distinct_configs_evidence_and_learning(self):
        import httpx
        from openai import OpenAI
        from project.game.actions import ActionProxy
        from project.run_hybrid_reflection import main as reflection_main
        from project.run_hybrid_economics import main as economics_main

        for main, strategy, field, review in (
                (reflection_main, "hybrid_reflection", "daily_reflection", "No prior day observed"),
                (economics_main, "hybrid_economics", "economics_json", json.dumps(economic_review()))):
            answers = iter([response(("set_daily_plan", {"reason": "Plan", **plan(**{field: review})})),
                            response(("use", {"reason": "Cut grass", "direction": 1}))])
            def reply(request):
                return httpx.Response(200, json=next(answers).model_dump())
            observations = iter([observed(grass=[(1, 0)]), observed(grass=[(1, 0)]), observed()])
            def game_reply(proxy, message, print_message=True):
                return json.dumps(next(observations)) if message.startswith("observe_v2") else "true"
            client = OpenAI(api_key="offline-test", http_client=httpx.Client(transport=httpx.MockTransport(reply)))
            with self.subTest(strategy=strategy), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "episode"
                with patch("openai.OpenAI", return_value=client), patch.dict(
                        "os.environ", {"OPENROUTER_API_KEY": "offline-test"}), patch("socket.create_connection"), \
                        patch.object(ActionProxy, "_post_message", game_reply), patch("subprocess.Popen"), \
                        contextlib.redirect_stdout(io.StringIO()):
                    code = main(["--provider", "openrouter", "--cut-grass", "1", "--max-steps", "2", "--settle-seconds", "0",
                                 "--model", "offline-model", "--disable-skill-synthesis", "--show-live-log",
                                 "--output-dir", str(output)])
                self.assertEqual(code, 0)
                config = json.loads((output / "config.json").read_text(encoding="utf-8"))
                summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
                learning = json.loads((output / "learning.json").read_text(encoding="utf-8"))
                evidence = json.loads((output / "daily_evidence.json").read_text(encoding="utf-8"))
                live = json.loads((output / "live_status.json").read_text(encoding="utf-8"))
                self.assertEqual(config["strategy"], strategy)
                self.assertNotIn("define_tool", config["tool_names"])
                self.assertEqual(summary["strategy"], strategy + "_api_only")
                self.assertEqual(len(learning["reviews"]), 1)
                self.assertEqual(len(evidence[0]["events"]), 1)
                self.assertEqual(live["learning"]["latest_review"], learning["reviews"][-1])
                self.assertIn("Daily review:", (output / "live.log").read_text(encoding="utf-8"))
