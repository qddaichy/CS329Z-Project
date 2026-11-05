"""Exercise planner refresh and full accounting without game or paid LLM calls."""
import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from project.agents.high_level import HighLevelAgent
from project.agents.react import GameTools, run_episode
from project.checks.check_react_baseline import response, state
from project.run_high_level import main as high_level_main


class HighLevelChecks(unittest.TestCase):
    def test_planning_is_periodic_and_included_in_costs(self):
        client = Mock()
        plan = response(("set_plan", {"steps": [
                                      {"goal": "Find forage", "verification": "Inventory contains forage"},
                                      {"goal": "Sell it", "verification": "Balance reaches target"}],
                                      "current_step": "Find forage", "reason": "Need income"}))
        action = response(("turn", {"reason": "Look", "direction": 0}))
        client.chat.completions.create.side_effect = [plan, action, action, plan, action]
        proxy = Mock()
        proxy.turn.return_value = None
        proxy.observe_state.return_value = json.dumps(state())
        agent = HighLevelAgent(client, "offline", plan_interval=2)
        events = []
        result = run_episode(agent, GameTools(proxy), "Earn gold", max_steps=3,
                             settle_seconds=0, emit=events.append)
        self.assertEqual(result["strategy"], "high_level_api_only")
        self.assertEqual(result["planner_calls"], 2)
        self.assertEqual(result["total_llm_calls"], 5)
        self.assertEqual(result["total_prompt_tokens"], 50)
        self.assertEqual(result["total_completion_tokens"], 25)
        self.assertEqual(len([event for event in events if event["type"] == "plan"]), 2)
        self.assertEqual(proxy.turn.call_count, 3)

    def test_invalid_plan_cannot_execute_game_actions(self):
        client = Mock()
        client.chat.completions.create.return_value = response(("set_plan", {
            "steps": [], "current_step": "x", "reason": "x"}))
        proxy = Mock()
        proxy.observe_state.return_value = json.dumps(state())
        result = run_episode(HighLevelAgent(client, "offline"), GameTools(proxy), "test",
                             max_steps=1, settle_seconds=0)
        self.assertEqual(result["stop_reason"], "error")
        self.assertEqual(result["planner_calls"], 1)
        self.assertEqual(result["action_calls"], 0)

    def test_plan_without_verification_is_rejected(self):
        client = Mock()
        client.chat.completions.create.return_value = response(("set_plan", {
            "steps": [{"goal": "Gather forage"}], "current_step": "Gather forage", "reason": "Earn gold"}))
        proxy = Mock()
        proxy.observe_state.return_value = json.dumps(state())
        result = run_episode(HighLevelAgent(client, "offline"), GameTools(proxy), "test",
                             max_steps=1, settle_seconds=0)
        self.assertEqual(result["stop_reason"], "error")
        self.assertEqual(result["action_calls"], 0)

    def test_high_level_entry_uses_shared_runner_and_records_strategy(self):
        import httpx
        from openai import OpenAI
        from project.game.actions import ActionProxy
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if payload["tools"][0]["function"]["name"] == "set_plan":
                result = response(("set_plan", {"steps": [{"goal": "Cut grass", "verification": "Grass tile disappears"}],
                    "current_step": "Cut grass", "reason": "Complete goal"}))
            else:
                result = response(("use", {"reason": "Cut grass", "direction": 1}))
            return httpx.Response(200, json=result.model_dump())

        observations = iter([state(grass=[(1, 0)]), state()])
        def game_reply(proxy, message, print_message=True):
            return json.dumps(next(observations)) if message.startswith("observe_v2") else "true"

        client = OpenAI(api_key="offline-test", http_client=httpx.Client(transport=httpx.MockTransport(respond)))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "episode"
            with patch("openai.OpenAI", return_value=client), patch.dict(
                    "os.environ", {"OPENROUTER_API_KEY": "offline-test"}), patch(
                    "socket.create_connection"), patch.object(ActionProxy, "_post_message", game_reply), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = high_level_main(["--provider", "openrouter", "--cut-grass", "1", "--max-steps", "1",
                    "--settle-seconds", "0", "--model", "offline-model", "--output-dir", str(output)])
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            config = json.loads((output / "config.json").read_text(encoding="utf-8"))
            log = (output / "live.log").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertEqual(config["strategy"], "high_level")
        self.assertEqual(summary["strategy"], "high_level_api_only")
        self.assertEqual(summary["total_llm_calls"], 2)
        self.assertIn("High-level plan:", log)
        self.assertEqual(len(requests), 2)

