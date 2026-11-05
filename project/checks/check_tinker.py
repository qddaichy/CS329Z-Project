"""Provider-wire conversion, strict execution boundaries and experiment wiring."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from project.llm_client import TinkerClient, TinkerOpenAIClient, convert_messages
from project.experiment_runner import parse_args
from project.checks.check_hybrid_variations import observed, plan, economic_review


def native(name='wait',args=None,stop='tool_use'):
    return {'id':'msg_test','model':'Qwen/Qwen3.8-27B','type':'message','role':'assistant',
            'stop_reason':stop,'content':[{'type':'tool_use','id':'tool_test','name':name,
                'input':args or {'reason':'Observe','duration_ms':50}}],
            'usage':{'input_tokens':100,'output_tokens':20}}


class TinkerChecks(unittest.TestCase):
    def test_turn_conversion_preserves_ids_json_and_results(self):
        messages=[{'role':'system','content':'Rules'}, {'role':'user','content':'Task'},
            {'role':'user','content':'Observation'}, {'role':'assistant','content':None,'tool_calls':[
                {'id':'c1','function':{'name':'wait','arguments':'{"reason":"x","duration_ms":50}'}}]},
            {'role':'tool','tool_call_id':'c1','content':'{"ok":true}'},
            {'role':'user','content':'Next observation'}]
        before=copy.deepcopy(messages);system,turns=convert_messages(messages)
        self.assertEqual(system,'Rules')
        self.assertEqual([x['role'] for x in turns],['user','assistant','user'])
        self.assertEqual(turns[1]['content'][0]['id'],'c1')
        self.assertEqual(turns[1]['content'][0]['input']['duration_ms'],50)
        self.assertEqual(turns[2]['content'][0]['tool_use_id'],'c1')
        self.assertEqual(messages,before)

    def test_wire_schema_auth_usage_and_no_credential_in_trace(self):
        from project.agents.react import tool_schemas
        requests=[];events=[]
        def reply(request):
            requests.append(request)
            return httpx.Response(200,json=native())
        with TinkerClientForTest(reply) as client:
            client.trace=events.append
            result=client.chat.completions.create(model='Qwen/Qwen3.8-27B',
                messages=[{'role':'user','content':'Observe'}],tools=tool_schemas())
        body=json.loads(requests[0].content)
        self.assertTrue(str(requests[0].url).endswith('/anthropic/api/v1/messages'))
        self.assertEqual(requests[0].headers['x-api-key'],'secret-test-key')
        self.assertNotIn('thinking',body)
        self.assertEqual(body['output_config'],{'effort':'low'})
        self.assertEqual(body['tool_choice'],{'type':'any','disable_parallel_tool_use':True})
        self.assertIn('input_schema',body['tools'][0])
        self.assertNotIn('strict',body['tools'][0])
        self.assertEqual(result.usage.total_tokens,120)
        self.assertEqual(client.usage_incomplete_count,0)
        self.assertEqual(result.choices[0].message.tool_calls[0].id,'tool_test')
        self.assertNotIn('secret-test-key',json.dumps(events))

    def test_truncation_and_multiple_calls_are_not_silently_executed(self):
        from project.agents.react import tool_schemas
        for stop in ['max_tokens','tool_use']:
            data=native(stop=stop)
            if stop=='tool_use':data['content'].append({**data['content'][0],'id':'second'})
            with TinkerClientForTest(lambda r:httpx.Response(200,json=data)) as client:
                result=client.create(model='Qwen/Qwen3.8-27B',messages=[{'role':'user','content':'Task'}],tools=tool_schemas())
            self.assertEqual(len(result.choices[0].message.tool_calls),0 if stop=='max_tokens' else 2)
            # Multi-call output remains intact for the existing harness to reject.

    def test_missing_usage_and_http_errors_fail_without_retry(self):
        for status in [200,401,429,500]:
            requests=[];data=native();data.pop('usage')
            def reply(request):
                requests.append(request)
                return httpx.Response(status,json=data)
            with TinkerClientForTest(reply) as client, self.assertRaises((ValueError,httpx.HTTPStatusError)):
                client.create(model='Qwen/Qwen3.8-27B',messages=[{'role':'user','content':'x'}],tools=[])
            self.assertEqual(len(requests),1)
            self.assertEqual(client.usage_incomplete_count,1)

    def test_provider_preset_and_explicit_config(self):
        self.assertEqual(parse_args(['--provider','tinker']).llm_config.name,'tinker_qwen.json')
        self.assertEqual(parse_args(['--provider','openrouter']).llm_config.name,'llm.json')
        self.assertEqual(parse_args(['--llm-config','custom.json']).llm_config,Path('custom.json'))

    def test_cache_creation_and_reads_are_included_in_prompt_tokens(self):
        data=native();data['usage'].update(input_tokens=0,cache_creation_input_tokens=100,cache_read_input_tokens=50)
        with TinkerClientForTest(lambda r:httpx.Response(200,json=data)) as client:
            result=client.create(model='Qwen/Qwen3.8-27B',messages=[{'role':'user','content':'Task'}],tools=[])
        self.assertEqual(result.usage.prompt_tokens,150)
        self.assertEqual(result.usage.total_tokens,170)
        self.assertEqual(result.usage.prompt_tokens_details.cached_tokens,50)

    def test_invalid_cache_usage_does_not_look_complete(self):
        data=native();data['usage']['cache_read_input_tokens']=-1
        with TinkerClientForTest(lambda r:httpx.Response(200,json=data)) as client:
            with self.assertRaises(ValueError):
                client.create(model='Qwen/Qwen3.8-27B',messages=[{'role':'user','content':'Task'}],tools=[])
            self.assertEqual(client.usage_incomplete_count,1)

    def test_transient_retry_is_bounded_and_preserves_unknown_usage(self):
        for statuses in ([502,200],[503,504,502]):
            queue=iter(statuses);requests=[]
            def reply(request):
                requests.append(request)
                return httpx.Response(next(queue),json=native())
            with TinkerClientForTest(reply,max_attempts=3) as client, patch('project.llm_client.time.sleep'):
                if statuses[-1]==200:
                    client.create(model='Qwen/Qwen3.8-27B',messages=[{'role':'user','content':'Task'}],tools=[])
                    self.assertEqual(client.usage_incomplete_count,1)
                else:
                    with self.assertRaises(httpx.HTTPStatusError):
                        client.create(model='Qwen/Qwen3.8-27B',messages=[{'role':'user','content':'Task'}],tools=[])
                    self.assertEqual(client.usage_incomplete_count,3)
                self.assertEqual(client.request_count,len(statuses))
                self.assertEqual(len(requests),len(statuses))

    def test_kimi_openai_request_disables_thinking_and_preserves_tool_calls(self):
        from project.agents.react import tool_schemas
        from project.checks.check_react_baseline import response
        requests=[];events=[]
        def reply(request):
            requests.append(request)
            return httpx.Response(200,json=response(('wait',{'reason':'test','duration_ms':50})).model_dump())
        client=TinkerOpenAIClient('secret-test-key',base_url='https://example.test/api/v1',
                                  thinking=False,transport=httpx.MockTransport(reply))
        with client:
            client.trace=events.append
            result=client.create(model='moonshotai/Kimi-K2.6',messages=[{'role':'user','content':'Task'}],tools=tool_schemas())
        payload=json.loads(requests[0].content)
        self.assertIs(payload['reasoning_effort'],False)
        self.assertEqual(requests[0].headers['authorization'],'Bearer secret-test-key')
        self.assertNotIn('secret-test-key',json.dumps(events))
        self.assertEqual(result.choices[0].message.tool_calls[0].function.name,'wait')
        self.assertEqual(result.usage.total_tokens,15)

    def test_kimi_rejects_unexpected_reasoning_and_discards_truncated_tool_calls(self):
        from project.checks.check_react_baseline import response
        data=response(('wait',{'reason':'test','duration_ms':50})).model_dump()
        data['choices'][0]['message']['reasoning_content']='unexpected reasoning'
        client=TinkerOpenAIClient('test',base_url='https://example.test/api/v1',thinking=False,
                                  transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data)))
        with client,self.assertRaises(ValueError):
            client.create(model='moonshotai/Kimi-K2.6',messages=[{'role':'user','content':'Task'}],tools=[])
        data['choices'][0]['message']['reasoning_content']=None
        data['choices'][0]['finish_reason']='length'
        client=TinkerOpenAIClient('test',base_url='https://example.test/api/v1',thinking=False,
                                  transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data)))
        with client:
            result=client.create(model='moonshotai/Kimi-K2.6',messages=[{'role':'user','content':'Task'}],tools=[])
        self.assertFalse(result.choices[0].message.tool_calls)

    def test_economic_agent_round_trip_exports_reviews_without_cash_forecasts(self):
        from project.experiment_runner import main
        from project.game.actions import ActionProxy
        answers=iter([native('set_daily_plan',{'reason':'Plan',**plan(economics_json=json.dumps(economic_review()))}),
                      native('use',{'reason':'Cut grass','direction':1})])
        requests=[]
        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json=next(answers))
        observations=iter([observed(grass=[(1,0)]),observed(grass=[(1,0)]),observed()])
        def game_reply(proxy,message,print_message=True):
            return json.dumps(next(observations)) if message.startswith('observe_v2') else 'true'
        with TinkerClientForTest(reply) as client, tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'run'
            with patch('project.llm_client.TinkerClient',return_value=client), \
                 patch.dict('os.environ',{'TINKER_API_KEY':'secret-test-key'}), \
                 patch('socket.create_connection'), patch.object(ActionProxy,'_post_message',game_reply):
                import contextlib,io
                with contextlib.redirect_stdout(io.StringIO()):
                    code=main(['--provider','tinker','--strategy','hybrid_economics','--cut-grass','1',
                               '--max-steps','2','--settle-seconds','0','--output-dir',str(output)])
            self.assertEqual(code,0)
            self.assertTrue(any(x['type']=='tool_result' for x in requests[1]['messages'][-1]['content']))
            settings=json.loads((output/'config.json').read_text(encoding='utf-8'))
            self.assertEqual(settings['llm_provider'],'tinker')
            self.assertEqual(settings['model'],'Qwen/Qwen3.8-27B')
            summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['prompt_tokens'],200)
            self.assertTrue(summary['provider_usage_complete'])
            self.assertEqual(summary['provider_requests'],2)
            self.assertNotIn('daily_forecast',summary['learning']['reviews'][0]['review'])
            self.assertIn('activities',summary['learning']['reviews'][0]['review'])
            self.assertNotIn('forecast_evaluations',summary['learning'])
            self.assertEqual(settings['economic_review_version'],4)
            self.assertEqual(len((output/'provider_io.jsonl').read_text(encoding='utf-8').splitlines()),4)
            self.assertNotIn('secret-test-key',(output/'provider_io.jsonl').read_text(encoding='utf-8'))


class TinkerClientForTest(TinkerClient):
    def __init__(self,reply,**kwargs):
        super().__init__('secret-test-key',thinking=True,transport=httpx.MockTransport(reply),**kwargs)
