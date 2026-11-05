"""Offline policy-boundary and episode tests; no API key or game is needed."""

import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from project.agents.react import (GameTools, ReActAgent, StateEvaluator, compact_observation, run_episode,
                            tool_schemas, validate_call)
from project.run_react import api_key_for_config, main, parse_args, task_settings


def state(grass=(), position=(0, 0), wood=0, location="Farm", visible=None):
    points = visible if visible is not None else [(0, 0), (1, 0), (2, 0)]
    return {"player": {"position": position, "location": location,
                       "inventory": [{"name": "Wood", "quantity": wood}]},
            "surroundingsdata": [{"position": list(p), "terrain_at_tile":
                                  "StardewValley.TerrainFeatures.Grass" if p in grass else ""}
                                 for p in points]}


def response(*calls):
    from openai.types.chat import ChatCompletion
    return ChatCompletion.model_validate({
        "id": "offline", "object": "chat.completion", "created": 0, "model": "gpt-4.1",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [
                {"id": f"call_{i}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments)}}
                for i, (name, arguments) in enumerate(calls)]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


class BaselineChecks(unittest.TestCase):
    def setup_episode(self, observations, responses):
        proxy = Mock()
        proxy.observe_state.side_effect = [json.dumps(s) for s in observations]
        proxy.use.return_value = None
        client = Mock()
        client.chat.completions.create.side_effect = responses
        agent = ReActAgent(client, "gpt-4.1", history_turns=2)
        game = GameTools(proxy)
        return proxy, client, agent, game

    def test_rejects_code_private_apis_wrong_types_and_extra_arguments(self):
        bad = [("exec", '{"reason":"x","code":"print(1)"}'),
               ("_post_message", '{"reason":"x","message":"set_money%999"}'),
               ("load_game", '{"reason":"x"}'),
               ("use", '{"reason":"x","direction":true}'),
               ("use", '{"reason":"x","direction":"__import__(\"os\")"}'),
               ("use", '{"reason":"x","direction":0,"code":"pass"}'),
               ("use", '{"reason":"x","direction":4}'),
               ("use", '[1,2]'), ("use", 'use(0)')]
        for name, args in bad:
            with self.subTest(name=name, args=args), self.assertRaises((ValueError, TypeError)):
                validate_call(name, args)

    def test_schemas_are_strict_and_match_dispatch(self):
        for tool in tool_schemas():
            function = tool["function"]
            self.assertTrue(function["strict"])
            parameters = function["parameters"]
            self.assertFalse(parameters["additionalProperties"])
            self.assertEqual(set(parameters["required"]), set(parameters["properties"]))

    def test_react_observes_after_one_action_and_verifies_last_step(self):
        before, after = state(grass=[(1, 0)]), state()
        proxy, client, agent, game = self.setup_episode([before, after], [
            response(("use", {"reason": "Cut adjacent grass.", "direction": 1}))])
        events = []
        summary = run_episode(agent, game, "cut grass", StateEvaluator("grass"),
                              max_steps=1, settle_seconds=0, emit=events.append)
        self.assertTrue(summary["success"])
        self.assertEqual(summary["stop_reason"], "success")
        self.assertEqual(summary["action_calls"], 1)
        self.assertEqual(summary["observation_calls"], 2)
        self.assertEqual(summary["prompt_tokens"], 10)
        proxy.use.assert_called_once_with(direction=1)
        self.assertEqual(proxy.method_calls[-1][0], "resume_game")
        request = client.chat.completions.create.call_args.kwargs
        self.assertFalse(request["parallel_tool_calls"])
        self.assertEqual(request["tool_choice"], "required")

    def test_sleep_dialogue_may_need_two_selections_before_overnight_wait(self):
        def snapshot(day, menu):
            return {**state(), "gamestate": {"year": 1, "season": "spring", "dayofmonth": day},
                    "menu": {"is_open": menu}}
        before, open_menu, closed, next_day = snapshot(1, False), snapshot(1, True), snapshot(1, False), snapshot(2, False)
        proxy, _, agent, game = self.setup_episode(
            [before, open_menu, open_menu, open_menu, closed, next_day, next_day],
            [response(("request_sleep", {"reason": "Sleep"})),
             response(("choose_option", {"reason": "Select", "option_index": 1, "quantity": 1, "direction": 0})),
             response(("choose_option", {"reason": "Confirm", "option_index": 1, "quantity": 1, "direction": 0}))])
        proxy.request_sleep.return_value = '{"ok":true}'
        proxy.choose_option.return_value = None
        events = []
        with patch("project.agents.react.time.sleep"):
            summary = run_episode(agent, game, "sleep", max_steps=3, settle_seconds=0, emit=events.append)
        self.assertEqual(summary["action_calls"], 3)
        self.assertEqual(sum(e["type"] == "waiting_for_day" for e in events), 1)
        self.assertEqual(proxy.choose_option.call_count, 2)
        self.assertNotEqual(summary["stop_reason"], "error")

    def test_sleep_rejection_is_reported_to_agent(self):
        proxy = Mock()
        proxy.request_sleep.return_value = '{"ok":false,"error":"Stand on your farmhouse bed first."}'
        result = GameTools(proxy).execute("request_sleep", {})
        self.assertFalse(result["ok"])
        self.assertIn("bed", result["error"])

    def test_batch_and_unknown_call_execute_nothing_and_consume_budget(self):
        calls = response(("use", {"reason": "one", "direction": 0}),
                         ("use", {"reason": "two", "direction": 1}))
        proxy, _, agent, game = self.setup_episode([state()] * 3, [calls,
            response(("exec", {"reason": "run code", "code": "bad"}))])
        summary = run_episode(agent, game, "test", max_steps=2, settle_seconds=0)
        self.assertEqual(summary["invalid_decisions"], 2)
        self.assertEqual(summary["action_calls"], 0)
        self.assertEqual(summary["llm_calls"], 2)
        proxy.use.assert_not_called()

    def test_finish_cannot_claim_measured_success(self):
        _, _, agent, game = self.setup_episode([state()], [response(("finish", {"reason": "Done"}))])
        summary = run_episode(agent, game, "get wood", StateEvaluator("collect", "Wood", 10))
        self.assertFalse(summary["success"])
        self.assertEqual(summary["stop_reason"], "agent_finished")
        self.assertEqual(summary["action_calls"], 0)

    def test_api_failure_is_returned_to_model_before_next_decision(self):
        proxy, client, agent, game = self.setup_episode([state()] * 2, [
            response(("use", {"reason": "try", "direction": 1})),
            response(("finish", {"reason": "Cannot continue"}))])
        proxy.use.side_effect = ConnectionError("transport failed")
        summary = run_episode(agent, game, "test", settle_seconds=0)
        self.assertEqual(summary["api_errors"], 1)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        result = next(m for m in messages if m["role"] == "tool")
        self.assertIn("transport failed", result["content"])
        self.assertFalse(json.loads(result["content"])["ok"])

    def test_llm_error_keeps_game_paused_and_emits_summary(self):
        proxy, _, agent, game = self.setup_episode([state()], [RuntimeError("model unavailable")])
        events = []
        summary = run_episode(agent, game, "test", emit=events.append)
        self.assertEqual(summary["stop_reason"], "error")
        self.assertEqual(summary["error"], "model unavailable")
        proxy.resume_game.assert_not_called()
        self.assertEqual(proxy.method_calls[-1][0], 'pause_game')
        self.assertEqual(events[-1]["type"], "summary")

    def test_collect_counts_new_inventory_only(self):
        evaluator = StateEvaluator("collect", "Wood", 10)
        self.assertFalse(evaluator.update(state(wood=50))["completed"])
        self.assertEqual(evaluator.update(state(wood=55))["quantity"], 5)
        self.assertTrue(evaluator.update(state(wood=60))["completed"])

    def test_missing_tiles_and_location_changes_do_not_count_as_cut_grass(self):
        evaluator = StateEvaluator("grass", count=2)
        evaluator.update(state(grass=[(1, 0)]))
        self.assertEqual(evaluator.update(state(visible=[(0, 0)]))["quantity"], 0)
        evaluator.update(state(grass=[(1, 0)]))
        self.assertEqual(evaluator.update(state(location="Town"))["quantity"], 0)
        evaluator.update(state(grass=[(1, 0)], location="Town"))
        self.assertEqual(evaluator.update(state(location="Town"))["quantity"], 1)
        evaluator.update(state(grass=[(1, 0)], location="Town"))
        self.assertEqual(evaluator.update(state(location="Town"))["quantity"], 1)

    def test_observe_radius_is_fixed_at_eight(self):
        proxy, _, agent, game = self.setup_episode([state()] * 3, [
            response(("observe_state", {"reason": "Refresh state", "size": 8})),
            response(("turn", {"reason": "Face north", "direction": 0}))])
        run_episode(agent, game, "look", max_steps=2, settle_seconds=0)
        self.assertEqual([c.args for c in proxy.observe_state.call_args_list], [(8,), (8,), (8,)])
        with self.assertRaises(ValueError):
            validate_call("observe_state", '{"reason":"Look farther","size":12}')
        with self.assertRaises(ValueError):
            game.execute("observe_state", {"size": 12})
        with self.assertRaises(ValueError):
            GameTools(proxy, radius=12)
        with self.assertRaises(SystemExit):
            parse_args(["--radius", "12"])

    def test_benchmark_metadata_and_unsupported_evaluator(self):
        goal, evaluator, metadata = task_settings(parse_args([
            "--task-name", "exploration_lite", "--task-id", "11"]))
        self.assertEqual(evaluator.target, "Wood")
        self.assertEqual(evaluator.count, 10)
        self.assertIn("Wood", goal)
        self.assertFalse(metadata["reset_performed"])
        with self.assertRaises(ValueError):
            task_settings(parse_args(["--task-name", "exploration_lite", "--task-id", "0"]))

    def test_craft_index_checked_before_api(self):
        proxy = Mock()
        game = GameTools(proxy, crafting_recipes=["Chest"])
        with self.assertRaises(ValueError):
            game.execute("craft", {"item_id": 1})
        proxy.craft.assert_not_called()

    def test_menu_and_world_are_read_only_and_return_structured_results(self):
        proxy = Mock()
        proxy.observe_menu.return_value = json.dumps({"Available": True, "Menu_Type": "DialogueBox",
                                                       "Details": {"Responses": [{"responseText": "Yes"}]}})
        proxy.observe_world_map.return_value = json.dumps({"Available": True, "Locations": [{"Name": "Farm"}]})
        game = GameTools(proxy)
        menu = game.execute("observe_menu", {})
        world = game.execute("observe_world_map", {})
        self.assertEqual(menu["data"]["details"]["responses"][0]["responsetext"], "Yes")
        self.assertEqual(world["data"]["locations"][0]["name"], "Farm")
        proxy.observe_menu.assert_called_once_with()
        proxy.observe_world_map.assert_called_once_with()
        proxy.observe_menu.return_value = "not-json"
        with self.assertRaisesRegex(RuntimeError, "update and restart"):
            game.execute("observe_menu", {})

    def test_gpt6_sdk_request_uses_compatible_reasoning_effort(self):
        import httpx
        from openai import OpenAI
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=response(("finish", {"reason": "Done"})).model_dump())

        client = OpenAI(api_key="offline-test", http_client=httpx.Client(
            transport=httpx.MockTransport(respond)))
        with client:
            ReActAgent(client, "openai/gpt-6-sol").decide("test", state(), None, [], [])
        self.assertEqual(requests[0]["model"], "openai/gpt-6-sol")
        self.assertEqual(requests[0]["reasoning_effort"], "none")
        self.assertFalse(requests[0]["parallel_tool_calls"])

    def test_terra_responses_tool_round_trip_and_token_accounting(self):
        import httpx
        from openai import OpenAI
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "resp_test", "object": "response", "created_at": 0,
                "model": "openai/gpt-6.1-terra", "parallel_tool_calls": False,
                "tool_choice": "required", "tools": [],
                "output": [{"type": "function_call", "id": "fc_test", "call_id": "call_1",
                            "name": "use", "arguments": '{"reason":"Cut grass","direction":2}'}],
                "usage": {"input_tokens": 30, "input_tokens_details": {"cached_tokens": 5},
                          "output_tokens": 10, "output_tokens_details": {"reasoning_tokens": 0},
                          "total_tokens": 40}})
        client = OpenAI(api_key="offline-test", http_client=httpx.Client(
            transport=httpx.MockTransport(respond)))
        with client:
            agent = ReActAgent(client, "openai/gpt-6.1-terra")
            first = agent.decide("test", state(), None, [], [])
            message = first.choices[0].message.model_dump(exclude_none=True)
            history = [[message, {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
                        {"role": "user", "content": json.dumps({"observation": state(), "evaluation": None})}]]
            agent.decide("test", state(), None, history, [])
        self.assertEqual(first.usage.prompt_tokens, 30)
        self.assertEqual(first.usage.completion_tokens, 10)
        self.assertEqual(first.choices[0].message.tool_calls[0].function.name, "use")
        self.assertEqual(requests[0]["reasoning"]["effort"], "none")
        self.assertFalse(requests[0]["parallel_tool_calls"])
        self.assertTrue(requests[0]["tools"][0]["strict"])
        self.assertTrue(any(item.get("type") == "function_call" and item["call_id"] == "call_1"
                            for item in requests[1]["input"]))
        self.assertTrue(any(item.get("type") == "function_call_output" and item["call_id"] == "call_1"
                            for item in requests[1]["input"]))

    def test_compact_observation_preserves_observed_tile_details(self):
        original = {"player": {"position": [2, 4], "inventory": []}, "surroundingsdata": [
            {"position": [2, 4], "terrain_at_tile": "", "tile_properties": "Walkable: True",
             "placeable": True, "npc_info": "", "crop_at_tile": None},
            {"position": [3, 4], "terrain_at_tile": "Grass", "tile_properties": "Walkable: True",
             "placeable": False, "npc_info": "Leah", "crop_at_tile": {"seed_name": "Parsnip"}},
            {"position": [4, 4], "exit_info": "Town", "tile_properties": ""}]}
        result = compact_observation(original)
        self.assertEqual(result["player"], original["player"])
        tiles = {tuple(position): {**group['fields'], 'position': position}
                 for group in result['observed_tile_groups'] for position in group['positions']}
        self.assertEqual(set(tiles), {(2, 4), (3, 4), (4, 4)})
        self.assertEqual(result["tile_property_legend"], ["Walkable: True"])
        tile = tiles[(3, 4)]
        self.assertEqual(result["tile_property_legend"][tile["property_id"]], "Walkable: True")
        self.assertEqual((tile["terrain_at_tile"], tile["placeable"], tile["npc_info"]), ("Grass", False, "Leah"))
        self.assertEqual(tile["crop_at_tile"]["seed_name"], "Parsnip")
        self.assertEqual(tiles[(4, 4)]["exit_info"], "Town")
        self.assertEqual(result["immediate_neighbors"]["right"]["terrain_at_tile"], "Grass")
        self.assertIsNone(result["immediate_neighbors"]["up"])

    def test_interaction_effect_distinguishes_observed_crop_change_from_acknowledgement(self):
        from project.agents.react import interaction_effect
        before = {'player': {'position': [1, 1], 'location': 'Farm', 'money': 500,
                             'inventory': [None]},
                  'farming': {'soils': [{'position': [1, 2], 'crop': {'ready_to_harvest': True}}]},
                  'menu': {'is_open': False}}
        import copy
        after = copy.deepcopy(before)
        after['farming']['soils'][0]['crop'] = None
        after['player']['inventory'][0] = {'name': 'Parsnip', 'quantity': 1}
        feedback = interaction_effect(before, after, 2)
        self.assertEqual(feedback['changes']['target_crop']['position'], [1, 2])
        self.assertEqual(feedback['changes']['inventory_slots'][0]['after']['name'], 'Parsnip')
        unchanged = interaction_effect(after, after, 2)
        self.assertEqual(unchanged['changes'], {})
        self.assertIn('does not prove failure', unchanged['note'])

    def test_tile_grouping_is_lossless_for_repeated_values(self):
        from project.agents.react import group_observed_tiles
        tiles = [{'position': [x, y], 'terrain_at_tile': 'Grass' if x == 0 else '',
                  'placeable': False, 'npc_info': {'name': 'Robin'} if (x, y) == (1, 1) else None}
                 for x in range(3) for y in range(3)]
        groups = group_observed_tiles(tiles)
        expanded = {tuple(p): {**g['fields'], 'position': p} for g in groups for p in g['positions']}
        self.assertEqual(expanded, {tuple(t['position']): t for t in tiles})
        self.assertLess(len(groups), len(tiles))

    def test_compact_history_excludes_old_tile_maps_but_preserves_results(self):
        client = Mock()
        prior_state = state(grass=[(1, 0)], position=(9, 9))
        previous = [{"role": "assistant", "content": "past"},
                    {"role": "tool", "tool_call_id": "call_0", "content": '{"ok":false}'},
                    {"role": "user", "content": json.dumps({"observation": prior_state,
                                                              "evaluation": {"quantity": 1}})}]
        agent = ReActAgent(client, "openai/gpt-6-luna", observation_format="compact")
        agent.decide("test", state(), None, [previous], [])
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        prior = json.loads(messages[-2]["content"])
        self.assertEqual(prior["previous_player"]["position"], [9, 9])
        self.assertEqual(prior["evaluation"]["quantity"], 1)
        self.assertNotIn("surroundingsdata", messages[-2]["content"])
        self.assertIn('"ok":false', messages[-3]["content"])
        self.assertIn("observed_tile_groups", json.loads(messages[-1]["content"])["observation"])
        self.assertEqual(client.chat.completions.create.call_args.kwargs["reasoning_effort"], "none")
        agent = ReActAgent(client, "openai/gpt-6-sol", observation_format="raw")
        agent.decide("test", state(), None, [previous], [])
        raw = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("surroundingsdata", raw[-2]["content"])
        self.assertIn("surroundingsdata", raw[-1]["content"])

    def test_missing_openrouter_key_does_not_fall_back_to_openai_key(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "other-provider-key"}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                api_key_for_config({"key_var": "OPENROUTER_API_KEY"})

    def test_cli_sdk_transport_and_reports_together(self):
        import httpx
        from openai import OpenAI
        from project.game.actions import ActionProxy
        requests = []

        def llm_request(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=response(("use", {
                "reason": "Cut adjacent grass", "direction": 1})).model_dump())

        rules = {"auto_catch": True, "minigame_seconds": 10}
        observations = iter([{**state(grass=[(1, 0)]), "fishing_rules": rules}, state()])
        commands = []

        def game_reply(proxy, message, print_message=True):
            commands.append(message)
            return json.dumps(next(observations)) if message.startswith("observe_v2") else "true"

        client = OpenAI(api_key="offline-test", http_client=httpx.Client(
            transport=httpx.MockTransport(llm_request)))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "episode"
            with patch("openai.OpenAI", return_value=client), patch.dict(
                    "os.environ", {"OPENROUTER_API_KEY": "offline-test"}), patch(
                    "socket.create_connection"), patch.object(
                    ActionProxy, "_post_message", game_reply), contextlib.redirect_stdout(io.StringIO()):
                code = main(["--provider", "openrouter", "--cut-grass", "1", "--max-steps", "1",
                             "--settle-seconds", "0", "--model", "offline-model",
                             "--output-dir", str(output)])
            self.assertEqual(code, 0)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            config = json.loads((output / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["fishing_rules"], rules)
            self.assertEqual(summary["fishing_rules"], rules)
            live_log = (output / "live.log").read_text(encoding="utf-8")
            self.assertIn("Reason: Cut adjacent grass", live_log)
            self.assertIn('"wire_calls":', live_log)
            self.assertTrue(summary["success"])
            self.assertEqual(summary["wire_calls"], len(commands))
            self.assertIn("turn%1", commands)
            self.assertIn("use", commands)
            events = [json.loads(line) for line in (output / "trajectory.jsonl").read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual([event["type"] for event in events],
                             ["initial_observation", "thinking", "decision", "step", "summary"])
            self.assertEqual(commands[-1], "resume")
            self.assertEqual(requests[0]["model"], "offline-model")
            self.assertFalse(requests[0]["parallel_tool_calls"])


if __name__ == "__main__":
    unittest.main()
