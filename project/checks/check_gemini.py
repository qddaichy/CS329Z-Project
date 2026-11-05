"""Verify Gemini signature, history, usage, and tool-call safety offline."""
import copy
import json
import unittest
import httpx
from project.llm_client import GeminiClient
from project.experiment_runner import parse_args
from project.checks.check_react_baseline import response


class GeminiChecks(unittest.TestCase):
    def test_signature_survives_two_tool_turns_and_thinking_is_low(self):
        from project.agents.react import tool_schemas
        native=response(('wait',{'reason':'test','duration_ms':50})).model_dump()
        native['choices'][0]['message']['tool_calls'][0]['extra_content']={'google':{'thought_signature':'signed-payload'}}
        requests=[]
        def reply(request):
            requests.append(json.loads(request.content));return httpx.Response(200,json=native)
        with GeminiClient('offline',base_url='https://example.test/v1beta/openai',thinking=True,
                          transport=httpx.MockTransport(reply)) as client:
            messages=[{'role':'user','content':'Task'}]
            r=client.create(model='gemini-3.8-flash',messages=messages,tools=tool_schemas())
            message=r.choices[0].message.model_dump(exclude_none=True)
            messages.extend([message,{'role':'tool','tool_call_id':message['tool_calls'][0]['id'],'content':'{"ok":true}'}])
            original=copy.deepcopy(messages)
            client.create(model='gemini-3.8-flash',messages=messages,tools=tool_schemas())
        self.assertEqual(messages,original)
        self.assertEqual(requests[1]['messages'][1]['tool_calls'][0]['extra_content']['google']['thought_signature'],'signed-payload')
        self.assertEqual(requests[1]['messages'][2]['name'],'wait')
        self.assertEqual(requests[0]['reasoning_effort'],'low')
        self.assertNotIn('temperature',requests[0])

    def test_missing_signature_rejected_before_tool_execution(self):
        data=response(('wait',{'reason':'test','duration_ms':50})).model_dump()
        with GeminiClient('offline',base_url='https://example.test',thinking=True,
                          transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
            with self.assertRaisesRegex(ValueError,'signature'):
                client.create(model='gemini-3.8-flash',messages=[{'role':'user','content':'Task'}],tools=[])

    def test_gemini_provider_uses_own_config(self):
        self.assertEqual(parse_args(['--provider','gemini']).llm_config.name,'gemini_flash.json')

    def test_minimal_is_supported_for_flash_lite_but_not_38(self):
        native=response(('wait',{'reason':'test','duration_ms':50})).model_dump()
        native['choices'][0]['message']['tool_calls'][0]['extra_content']={'google':{'thought_signature':'signature'}}
        requests=[]
        def reply(request):
            requests.append(json.loads(request.content));return httpx.Response(200,json=native)
        with GeminiClient('offline',base_url='https://example.test',thinking=True,reasoning_effort='minimal',
                          transport=httpx.MockTransport(reply)) as client:
            client.create(model='gemini-3.5-flash-lite',messages=[{'role':'user','content':'Task'}],tools=[])
            with self.assertRaisesRegex(ValueError,'does not support minimal'):
                client.create(model='gemini-3.8-flash',messages=[{'role':'user','content':'Task'}],tools=[])
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]['reasoning_effort'],'minimal')

    def test_invalid_gemini_effort_rejected(self):
        with self.assertRaises(ValueError):
            GeminiClient('offline',base_url='https://example.test',thinking=True,reasoning_effort='none')

    def test_total_tokens_include_provider_extra_thinking_usage(self):
        data=response(('wait',{'reason':'test','duration_ms':50})).model_dump()
        data['choices'][0]['message']['tool_calls'][0]['extra_content']={'google':{'thought_signature':'signature'}}
        data['usage'].update(prompt_tokens=75,completion_tokens=16,total_tokens=137)
        with GeminiClient('offline',base_url='https://example.test',thinking=True,
                          transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
            result=client.create(model='gemini-3.8-flash',messages=[{'role':'user','content':'Task'}],tools=[])
        self.assertEqual((result.usage.prompt_tokens,result.usage.completion_tokens,result.usage.total_tokens),(75,62,137))
        self.assertEqual(result.usage.provider_other_tokens,46)

