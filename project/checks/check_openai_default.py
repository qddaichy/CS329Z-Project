"""Verify the documented default uses OpenAI through a complete offline episode."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI
from project.checks.check_react_baseline import state, response
from project.experiment_runner import api_key_for_config, main, parse_args
from project.game.actions import ActionProxy


class OpenAIDefaultChecks(unittest.TestCase):
    def test_default_and_explicit_openai_use_same_official_preset(self):
        default = parse_args([])
        self.assertEqual(default.llm_config, parse_args(['--provider', 'openai']).llm_config)
        config = json.loads(default.llm_config.read_text())
        self.assertEqual(config['provider'], 'openai')
        self.assertEqual(config['base_url'], 'https://api.openai.com/v1')
        self.assertEqual(config['comp_model'], 'gpt-6-sol')

    def test_project_key_and_standard_key_fallback(self):
        config = {'key_var': 'OA_OPENAI_KEY'}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'standard'}, clear=True):
            self.assertEqual(api_key_for_config(config), 'standard')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'standard', 'OA_OPENAI_KEY': 'project'}, clear=True):
            self.assertEqual(api_key_for_config(config), 'project')

    def test_documented_default_sdk_and_verified_episode_together(self):
        requests = []
        def reply(request):
            requests.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, json=response(('use', {'reason': 'Cut adjacent grass', 'direction': 1})).model_dump())
        observations = iter([state(grass=[(1, 0)]), state()])
        def game_reply(proxy, message, print_message=True):
            return json.dumps(next(observations)) if message.startswith('observe_v2') else 'true'
        client = OpenAI(api_key='offline-test', http_client=httpx.Client(transport=httpx.MockTransport(reply)))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'episode'
            with patch('openai.OpenAI', return_value=client) as constructor, patch.dict(
                    os.environ, {'OA_OPENAI_KEY': 'offline-test'}), patch('socket.create_connection'), patch.object(
                    ActionProxy, '_post_message', game_reply), contextlib.redirect_stdout(io.StringIO()):
                code = main(['--provider', 'openai', '--cut-grass', '1', '--max-steps', '30',
                             '--max-seconds', '180', '--settle-seconds', '0', '--output-dir', str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(constructor.call_args.kwargs['base_url'], 'https://api.openai.com/v1')
            self.assertEqual(constructor.call_args.kwargs['api_key'], 'offline-test')
            url, payload = requests[0]
            self.assertEqual(url, 'https://api.openai.com/v1/chat/completions')
            self.assertEqual(payload['model'], 'gpt-6-sol')
            self.assertEqual(payload['reasoning_effort'], 'none')
            self.assertTrue(json.loads((output / 'summary.json').read_text())['success'])
            self.assertEqual(json.loads((output / 'config.json').read_text())['llm_provider'], 'openai')


if __name__ == '__main__':
    unittest.main()
