"""Verify synthesized tool policy, trial, execution, and budgets offline."""
import json
import unittest
from unittest.mock import Mock

from project.agents.hybrid import HybridAgent
from project.agents.react import GameTools, run_episode
from project.checks.check_react_baseline import response, state


def definition(name="step_east", steps=None, condition=None):
    return {"name": name, "description": "Turn and step east",
            "parameters_json": json.dumps({"direction": {"type": "integer", "minimum": 1, "maximum": 4}}),
            "steps_json": json.dumps(steps or [
                {"tool": "turn", "arguments": {"direction": 1}},
                {"tool": "move_step", "arguments": {"direction": "$direction"}},
                {"tool": "turn", "arguments": {"direction": 1}}]),
            "postcondition_json": json.dumps(condition or {"path": "player.position", "op": "changed"})}


class HybridChecks(unittest.TestCase):
    def test_baseline_has_no_daily_plan_gate(self):
        client = Mock()
        client.chat.completions.create.return_value = response(('turn', {'reason': 'Act', 'direction': 0}))
        agent = HybridAgent(client, 'offline')
        for day in (1, 2):
            s = {**state(), 'gamestate': {'year': 1, 'season': 'spring', 'dayofmonth': day}}
            agent.decide('Earn gold', s, None, [], [])
            self.assertFalse(agent.daily_plan_required)
            self.assertIn('define_code_tool', {x['function']['name'] for x in agent.available_tool_schemas()})
            self.assertNotIn('set_daily_plan', {x['function']['name'] for x in agent.available_tool_schemas()})
            agent.validate_tool_call('turn', '{"reason":"x","direction":0}')
        request = client.chat.completions.create.call_args.kwargs
        self.assertNotIn('Current daily plan:', request['messages'][1]['content'])
        self.assertNotIn('set_daily_plan', agent.system_prompt())
        with self.assertRaises(ValueError): agent.set_daily_plan({}, s)

    def test_baseline_defines_skill_without_daily_plan(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            response(('define_tool', {'reason': 'Create skill', **definition()})),
            response(("finish", {"reason": "Stop test"}))]
        s = {**state(), "gamestate": {"year": 1, "season": "spring", "dayofmonth": 1}}
        proxy = Mock()
        proxy.observe_state.return_value = json.dumps(s)
        result = run_episode(HybridAgent(client, "offline"), GameTools(proxy), "test", max_steps=2, settle_seconds=0)
        self.assertEqual(result["stop_reason"], "agent_finished")
        self.assertEqual(result["daily_plan_calls"], 0)
        self.assertEqual(result['tool_definitions'], 1)
        self.assertEqual(result["primitive_calls"], 0)
        self.assertEqual(result['daily_plans'], [])

    def test_only_validated_api_recipes_can_be_defined(self):
        agent = HybridAgent(Mock(), "offline")
        for tool in ("exec", "set_money", "request_sleep", "run_tool"):
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                agent.define_tool(definition(steps=[{"tool": tool, "arguments": {}},
                                                    {"tool": "turn", "arguments": {"direction": 1}}]))
        self.assertEqual(agent.library_snapshot(), {})
        with self.assertRaises(ValueError):
            agent.define_tool(definition(steps=[{"tool": "turn", "arguments": {"direction": 1, "reason": "bad"}},
                                                {"tool": "turn", "arguments": {"direction": 1}}]))

    def test_wait_in_recipe_is_bounded_and_runs_in_order(self):
        agent = HybridAgent(Mock(), "offline")
        steps = [{"tool": "wait", "arguments": {"duration_ms": 50}},
                 {"tool": "turn", "arguments": {"direction": 1}}]
        agent.define_tool(definition(steps=steps))
        before = state()
        after = state()
        after["player"]["position"] = [1, 0]
        executor = Mock(side_effect=[({"ok": True}, before), ({"ok": True}, after)])
        result = agent.run_tool({"name": "step_east", "arguments_json": '{"direction":2}'},
                                before, executor, trial=True, completed=lambda: False)
        self.assertTrue(result["verified"])
        self.assertEqual([call.args[0] for call in executor.call_args_list], ["wait", "turn"])
        for ms in (0, 10001):
            with self.subTest(ms=ms), self.assertRaises(ValueError):
                HybridAgent(Mock(), "offline").define_tool(definition(steps=[
                    {"tool": "wait", "arguments": {"duration_ms": ms}}, steps[1]]))

    def test_trial_failure_is_not_promoted_and_run_requires_verified(self):
        agent = HybridAgent(Mock(), "offline")
        agent.define_tool(definition())
        args = {"name": "step_east", "arguments_json": '{"direction":2}'}
        execute = Mock(return_value=({"ok": True}, state()))
        with self.assertRaises(ValueError):
            agent.run_tool(args, state(), execute, trial=False, completed=lambda: False)
        result = agent.run_tool(args, state(), execute, trial=True, completed=lambda: False)
        self.assertFalse(result["postcondition_passed"])
        self.assertFalse(result["verified"])
        with self.assertRaises(ValueError):
            agent.run_tool(args, state(), execute, trial=False, completed=lambda: False)

    def test_equal_condition_requires_an_observed_transition(self):
        agent = HybridAgent(Mock(), "offline")
        agent.define_tool(definition(condition={"path": "player.location", "op": "equals", "value": "Farm"}))
        result = agent.run_tool({"name": "step_east", "arguments_json": '{"direction":2}'}, state(),
                                Mock(return_value=({"ok": True}, state())), trial=True, completed=lambda: False)
        self.assertFalse(result["postcondition_passed"])
        self.assertFalse(result["verified"])

    def test_failed_primitive_stops_remaining_steps(self):
        agent = HybridAgent(Mock(), "offline")
        agent.define_tool(definition())
        execute = Mock(return_value=({"ok": False, "error": "blocked"}, state()))
        result = agent.run_tool({"name": "step_east", "arguments_json": '{"direction":2}'}, state(),
                                execute, trial=True, completed=lambda: False)
        self.assertFalse(result["ok"])
        self.assertEqual(execute.call_count, 1)
        self.assertFalse(agent.library["step_east"]["verified"])

    def test_trial_and_reuse_stop_at_independent_goal_mid_tool(self):
        proxy = Mock()
        proxy.turn.return_value = None
        proxy.move_step.return_value = None
        observations = [state(position=(0, 0)), state(position=(0, 0)),
                        state(position=(0, 0)), state(position=(1, 0)), state(position=(1, 0)),
                        state(position=(1, 0)), state(position=(1, 0)), state(position=(1, 0)),
                        state(position=(2, 0)), state(position=(2, 0)),
                        state(position=(2, 0))]
        proxy.observe_state.side_effect = [json.dumps(s) for s in observations]
        client = Mock()
        args = {"name": "step_east", "arguments_json": '{"direction":2}'}
        client.chat.completions.create.side_effect = [
            response(("define_tool", {"reason": "Make reusable step", **definition()})),
            response(("test_tool", {"reason": "Test it", **args})),
            response(("run_tool", {"reason": "Use it again", **args}))]

        class Evaluator:
            def update(self, current):
                return {"completed": current["player"]["position"] == [2, 0]}

        events = []
        agent = HybridAgent(client, "offline")
        summary = run_episode(agent, GameTools(proxy), "Move twice", Evaluator(), max_steps=3,
                              max_primitive_actions=6, settle_seconds=0, emit=events.append)
        self.assertTrue(summary["success"])
        self.assertTrue(agent.library["step_east"]["verified"])
        self.assertEqual(summary["primitive_calls"], 5)
        self.assertEqual((summary["tool_definitions"], summary["tool_trials"], summary["tool_uses"]), (1, 1, 1))
        self.assertEqual(proxy.turn.call_count, 3)  # Last step is skipped on verified task completion.
        self.assertEqual(len([event for event in events if event["type"] == "tool_primitive"]), 5)

    def test_primitive_budget_stops_tool_midway(self):
        proxy = Mock()
        proxy.turn.return_value = None
        proxy.move_step.return_value = None
        proxy.observe_state.return_value = json.dumps(state())
        client = Mock()
        client.chat.completions.create.side_effect = [
            response(("define_tool", {"reason": "Build", **definition()})),
            response(("test_tool", {"reason": "Try", "name": "step_east", "arguments_json": '{"direction":2}'}))]
        agent = HybridAgent(client, "offline")
        result = run_episode(agent, GameTools(proxy), "test", max_steps=4,
                             max_primitive_actions=1, settle_seconds=0)
        self.assertEqual(result["stop_reason"], "max_primitive_actions")
        self.assertEqual(result["primitive_calls"], 1)
        self.assertEqual(proxy.move_step.call_count, 0)
        self.assertFalse(agent.library["step_east"]["verified"])

