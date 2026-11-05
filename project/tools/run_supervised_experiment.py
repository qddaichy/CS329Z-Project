"""Archive an experiment and apply explicitly queued, logged movement assistance.

The supervision layer does not change agent prompts or choose economic actions.
Write control.json in the output directory to pause, stop, or queue movement.
"""

import copy
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

from project import experiment_runner as runner
from project.agents.daily_experience import snapshot
from project.agents.react import validate_call
from project.paths import PROJECT_ROOT, REPO_ROOT


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    recovery_source = None
    recovery_step = None
    if '--resume-from' in argv:
        index = argv.index('--resume-from')
        recovery_source = Path(argv[index + 1]).resolve()
        del argv[index:index + 2]
        index = argv.index('--resume-through-step')
        recovery_step = int(argv[index + 1])
        del argv[index:index + 2]
    if '--output-dir' in argv:
        output = Path(argv[argv.index('--output-dir') + 1]).resolve()
    else:
        output = REPO_ROOT / 'runs/hybrid_economics' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        argv += ['--output-dir', str(output)]
    original = runner.run_episode

    def supervised(agent, game, goal, evaluator, max_steps, max_seconds, settle_seconds, pause, emit,
                   max_primitive_actions=None):
        recovery = None
        if recovery_source is not None:
            anchor = None
            replayed = 0
            for line in (recovery_source / 'trajectory.jsonl').open(encoding='utf-8'):
                event = json.loads(line)
                if event.get('step', 0) > recovery_step:
                    break
                if event.get('type') == 'summary':
                    continue
                action, result = event.get('action'), event.get('result') or {}
                if event.get('type') == 'step':
                    if action == 'define_tool' and result.get('ok'):
                        agent.define_tool(event['arguments'])
                    elif action == 'define_code_tool' and result.get('ok'):
                        agent.define_code_tool(event['arguments'])
                    elif action in {'test_tool', 'run_tool'}:
                        tool = agent.library[event['arguments']['name']]
                        tool['trials' if action == 'test_tool' else 'uses'] += 1
                        if 'postcondition_passed' in result:
                            agent._tool_outcome(tool, result['postcondition_passed'],
                                None if result['postcondition_passed'] else 'Declared postcondition did not pass; inspect before retesting.')
                        elif result.get('interrupted'):
                            tool['last_status'] = 'interrupted'
                            tool['last_error'] = result.get('note')
                        elif not result.get('ok'):
                            agent._tool_outcome(tool, False, result.get('error'))
                    elif action == 'set_daily_plan' and result.get('ok'):
                        agent.daily_plans.append(copy.deepcopy(result['plan']))
                        if result.get('review'):
                            agent.reviews.append(copy.deepcopy(result['review']))
                        if result.get('daily_reflection') and hasattr(agent, 'general_reflections'):
                            agent.general_reflections.append(copy.deepcopy(result['daily_reflection']))
                    elif action == 'update_economic_knowledge' and result.get('ok'):
                        agent.reviews.append(copy.deepcopy(result['review']))
                if event.get('type') == 'external_intervention' and event.get('state'):
                    agent.experience.previous = snapshot(event['state'])
                else:
                    agent.record_event(copy.deepcopy(event))
                if event.get('state'):
                    anchor = event
                replayed += 1
            if anchor is None or anchor.get('step') != recovery_step:
                raise ValueError('No exact state checkpoint at the requested recovery step.')
            fresh = game.observe()
            expected = anchor['state']
            for key in ['year', 'season', 'dayofmonth']:
                if fresh['gamestate'][key] != expected['gamestate'][key]:
                    raise ValueError('Loaded recovery date differs from the checkpoint.')
            if fresh['player']['money'] != expected['player']['money']:
                raise ValueError('Loaded recovery cash differs from the checkpoint.')
            from project.evaluation.profit_horizon import absolute_day
            progress = anchor['evaluation']
            evaluator.initial_day = absolute_day(fresh) - progress['elapsed_game_days']
            evaluator.last_day = absolute_day(fresh)
            evaluator.initial_balance = progress['initial_balance']
            evaluator.daily_balances = copy.deepcopy(progress['daily_balances'])
            agent.experience.previous = snapshot(fresh)
            recovery = {'source':str(recovery_source), 'through_step':recovery_step,
                'replayed_events':replayed, 'restored_date':fresh['gamestate'],
                'restored_cash':fresh['player']['money'],
                'note':'Rolled-back game actions and agent memory after the checkpoint are discarded from the continuation; their costs remain in the original segment. Short action history restarts empty.'}
            atomic_json(output / 'recovery.json', recovery)
        usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'recorded_decisions': 0}
        interventions = []
        last_state = None
        seen_dates = set()
        archived = output / 'source_snapshot'
        archived.mkdir()
        manifest = {}
        for folder in ['agents', 'evaluation', 'game', 'config']:
            for path in (PROJECT_ROOT / folder).rglob('*'):
                if path.is_file() and path.suffix in {'.py', '.json'}:
                    target = archived / path.relative_to(PROJECT_ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    manifest[str(path.relative_to(PROJECT_ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [PROJECT_ROOT / 'experiment_runner.py', PROJECT_ROOT / 'llm_client.py', Path(__file__)]:
            shutil.copy2(path, archived / path.name)
            manifest[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        fixture_paths = sorted((REPO_ROOT / '.tools/profit-test').glob('*/fixture.json'))
        if fixture_paths:
            shutil.copy2(fixture_paths[-1], output / 'fixture.json')
        atomic_json(output / 'provenance.json', {'created_at': now(), 'argv': argv,
                    'source_sha256': manifest, 'assistance_authorized': True,
                    'assistance_policy': 'Only explicit queued movement; all interventions logged separately.',
                    'recovery': recovery})

        def log_json(name, value):
            with (output / name).open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(value, ensure_ascii=False, default=str) + '\n')

        # Record only API payloads/results, never credentials or HTTP headers.
        def archive_create(create, endpoint):
            def wrapped(*args, **kwargs):
                request_id = usage['recorded_decisions'] + 1
                log_json('model_io.jsonl', {'type': 'request', 'id': request_id,
                                          'at': now(), 'endpoint': endpoint, 'payload': kwargs})
                try:
                    response = create(*args, **kwargs)
                except Exception as error:
                    log_json('model_io.jsonl', {'type': 'request_error', 'id': request_id,
                                              'at': now(), 'error': str(error)})
                    raise
                log_json('model_io.jsonl', {'type': 'response', 'id': request_id, 'at': now(),
                                          'payload': response.model_dump(exclude_none=True)})
                return response
            return wrapped

        agent.client.responses.create = archive_create(agent.client.responses.create, 'responses')
        agent.client.chat.completions.create = archive_create(agent.client.chat.completions.create, 'chat.completions')
        original_decide = agent.decide

        def decide(goal, state, progress, history, crafting_recipes):
            control = output / 'control.json'
            while control.exists():
                request = json.loads(control.read_text(encoding='utf-8'))
                if request.get('stop'):
                    raise KeyboardInterrupt
                if request.get('pause'):
                    atomic_json(output / 'control_status.json', {'at': now(), 'paused': True})
                    time.sleep(.5)
                    continue
                moves = request.get('moves', [])
                if not isinstance(moves, list) or len(moves) > 8:
                    raise ValueError('At most eight explicit movement commands per intervention.')
                reason = request.get('reason')
                if moves and (not isinstance(reason, str) or not reason.strip()):
                    raise ValueError('Intervention requires a recorded reason.')
                validated = []
                for move in moves:
                    if move.get('action') not in {'move', 'move_step', 'turn'}:
                        raise ValueError('Supervision permits movement/turning only.')
                    _, params = validate_call(move['action'], json.dumps({'reason': reason, **move['arguments']}))
                    validated.append((move['action'], params))
                for name, params in validated:
                    before = game.observe()
                    record = {'id': len(interventions) + 1, 'at': now(), 'reason': reason,
                              'action': name, 'arguments': params, 'before': before}
                    log_json('interventions.jsonl', {'type': 'started', **record})
                    game.proxy.resume_game()
                    try:
                        record['result'] = game.execute(name, params)
                        time.sleep(settle_seconds)
                    except Exception as error:
                        record['error'] = str(error)
                    finally:
                        if pause:
                            game.proxy.pause_game()
                    after = game.observe()
                    record['after'] = after
                    record['evaluation'] = evaluator.update(after) if evaluator else None
                    interventions.append(record)
                    log_json('interventions.jsonl', {'type': 'completed', **record})
                    emit({'type': 'external_intervention', **record, 'state': after})
                    state.clear()
                    state.update(after)
                    if progress is not None and record['evaluation'] is not None:
                        progress.clear()
                        progress.update(record['evaluation'])
                    agent.experience.previous = snapshot(after)
                    history.append([{'role': 'user', 'content': json.dumps({
                        'supervisor_intervention': {'reason': reason, 'action': name, 'arguments': params,
                                                    'result': record.get('result'), 'error': record.get('error')},
                        'notice': 'This external movement was assistance, not an autonomous agent achievement.'})}])
                control.unlink(missing_ok=True)
                atomic_json(output / 'control_status.json', {'at': now(), 'paused': False,
                                                           'applied_interventions': len(interventions)})
            return original_decide(goal, state, progress, history, crafting_recipes)

        agent.decide = decide

        def checkpoint(event):
            nonlocal last_state
            emit(event)
            if event.get('usage'):
                usage['recorded_decisions'] += 1
                for key in ['prompt_tokens', 'completion_tokens']:
                    usage[key] += event['usage'].get(key, 0)
            if event.get('state'):
                last_state = event['state']
            if last_state is None or event['type'] not in {'initial_observation', 'step', 'summary'}:
                return
            current = {'updated_at': now(), 'event_type': event['type'], 'step': event.get('step'),
                       'usage': usage, 'state': last_state,
                       'evaluation': event.get('evaluation'), 'interventions': len(interventions)}
            atomic_json(output / 'checkpoint.json', current)
            atomic_json(output / 'tools.json', agent.library_snapshot())
            atomic_json(output / 'learning.json', agent.learning_snapshot())
            atomic_json(output / 'daily_evidence.json', agent.experience.export())
            clock = last_state.get('gamestate', {})
            date = tuple(clock.get(k) for k in ['year', 'season', 'dayofmonth'])
            if date not in seen_dates:
                seen_dates.add(date)
                log_json('daily_checkpoints.jsonl', current)
                print('DAILY CHECKPOINT:', json.dumps({'date': date, 'cash': last_state['player']['money'],
                       'usage': usage, 'interventions': len(interventions)}), flush=True)

        summary = original(agent, game, goal, evaluator, max_steps, max_seconds, settle_seconds, pause,
                           checkpoint, max_primitive_actions=max_primitive_actions)
        summary.update(intervention_count=len(interventions),
                       assistance_mode='supervised_movement_allowed',
                       autonomous=len(interventions) == 0)
        if recovery is not None:
            summary['recovery'] = recovery
            summary['autonomous'] = False
        atomic_json(output / 'assistance_summary.json', {'intervention_count': len(interventions),
                    'autonomous': len(interventions) == 0,
                    'actions': [{k:v for k,v in x.items() if k not in {'before','after'}} for x in interventions]})
        if last_state:
            atomic_json(output / 'final_state.json', last_state)
        return summary

    runner.run_episode = supervised
    return runner.main(argv, default_strategy='hybrid_economics')


if __name__ == '__main__':
    raise SystemExit(main())
