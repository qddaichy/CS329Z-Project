"""Two small live tool round trips; never connects to the game."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import load_dotenv
from project.experiment_runner import api_key_for_config
from project.llm_client import create_client
from project.paths import ENV_FILE, PROJECT_ROOT, REPO_ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm-config',type=Path,default=PROJECT_ROOT/'config/tinker_qwen.json')
    args=parser.parse_args()
    load_dotenv(ENV_FILE)
    config=json.loads(args.llm_config.read_text(encoding='utf-8'))
    if config.get('provider')!='tinker':raise ValueError('Expected a Tinker config.')
    key=api_key_for_config(config)
    client=create_client(config,key,60)
    output=REPO_ROOT/'.tools/tinker-smoke'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True)
    def trace(event):
        with (output/'provider_io.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False)+'\n')
    client.trace=trace
    tools=[{'type':'function','function':{'name':'report_connection','description':'Report the requested test stage.',
        'strict':True,'parameters':{'type':'object','properties':{'stage':{'type':'string','enum':['first','second']}},
                                  'required':['stage'],'additionalProperties':False}}}]
    messages=[{'role':'system','content':'Call report_connection exactly once with the requested stage. Do not write code.'}]
    report={'provider':'tinker','model':config['comp_model'],'thinking':config.get('thinking'),
            'reasoning_effort':config.get('reasoning_effort'),
            'passed':False,'game_calls':0,'calls':[]}
    try:
        for stage in ['first','second']:
            messages.append({'role':'user','content':f'Report stage {stage}.'})
            result=client.chat.completions.create(model=config['comp_model'],messages=messages,
                tools=tools,tool_choice='required',parallel_tool_calls=False,temperature=0,max_completion_tokens=256)
            report['calls'].append(result.model_dump(exclude_none=True))
            calls=result.choices[0].message.tool_calls or []
            assert len(calls)==1 and calls[0].function.name=='report_connection','Expected one report_connection call'
            assert json.loads(calls[0].function.arguments)=={'stage':stage},'Incorrect tool arguments'
            messages.extend([result.choices[0].message.model_dump(exclude_none=True),
                {'role':'tool','tool_call_id':calls[0].id,'content':json.dumps({'ok':True,'stage':stage})}])
        report['passed']=True
    except Exception as error:
        report['error']=str(error)
        raise
    finally:
        client.close()
        report['usage_complete']=client.usage_incomplete_count == 0
        report['total_tokens']=sum(x['usage']['total_tokens'] for x in report['calls'])
        (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='calls'},ensure_ascii=False),flush=True)
        print('Report:',output/'result.json',flush=True)


if __name__=='__main__':main()
