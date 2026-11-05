"""Summarize a recorded run for reporting without calling a model or game API."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path


def analyze(output):
    config = json.loads((output / 'config.json').read_text(encoding='utf-8'))
    recovery = json.loads((output / 'recovery.json').read_text(encoding='utf-8')) if (output / 'recovery.json').exists() else None
    def iter_events():
        if recovery:
            source = Path(recovery['source'])
            for line in (source / 'trajectory.jsonl').open(encoding='utf-8'):
                event = json.loads(line)
                if event.get('step', 0) > recovery['through_step']:
                    break
                if event.get('type') != 'summary':
                    yield event
        for line in (output / 'trajectory.jsonl').open(encoding='utf-8'):
            if line.strip():
                yield json.loads(line)
    daily = {}
    usage = Counter()
    decisions = Counter()
    primitives = Counter()
    invalid = 0
    failed_actions = 0
    initial = final = None
    summary = None
    events = 0
    trial_events = []
    crop_observations = {}
    retained_interventions = 0
    for event in iter_events():
        if event.get('type') == 'external_intervention':
            retained_interventions += 1
        events += 1
        if event.get('usage'):
            for key in ['prompt_tokens', 'completion_tokens', 'total_tokens']:
                usage[key] += event['usage'].get(key, 0)
            usage['responses_with_usage'] += 1
        if event.get('type') == 'summary':
            summary = event
        if event.get('type') == 'decision':
            decisions[event['action']] += 1
        if event.get('type') == 'step' and event.get('error'):
            invalid += 1
        state = event.get('state')
        if not state:
            continue
        initial = initial or state
        final = state
        clock, player = state.get('gamestate', {}), state.get('player', {})
        date = tuple(clock.get(key) for key in ['year', 'season', 'dayofmonth'])
        label = f'{date[0]} {date[1]} {date[2]}'
        row = daily.setdefault(label, {'date': list(date), 'first_cash': player.get('money'),
            'last_cash': player.get('money'), 'first_time': clock.get('time'),
            'last_time': clock.get('time'), 'decisions': 0, 'primitive_actions': 0,
            'review_revisions': 0, 'failed_actions': 0})
        row['last_cash'], row['last_time'] = player.get('money'), clock.get('time')
        farming = state.get('farming') or {}
        if player.get('location') == 'Farm' and isinstance(farming.get('soils'), list):
            soils = farming['soils']
            crops = [x for x in soils if x.get('crop')]
            crop_observations[label] = {'date': list(date), 'time': clock.get('time'),
                'soil_tiles': len(soils), 'crop_tiles': len(crops),
                'watered_crop_tiles': sum(bool(x.get('watered')) for x in crops),
                'mature_crop_tiles': sum(bool(x['crop'].get('ready_to_harvest')) for x in crops),
                'dead_crop_tiles': sum(bool(x['crop'].get('dead')) for x in crops),
                'crop_types': dict(Counter(x['crop'].get('harvest_name') for x in crops)),
                'scope': farming.get('scope'), 'note': 'Last observed farm snapshot on this date, not necessarily end-of-day.'}
        result = event.get('result', {})
        action = event.get('action')
        if event.get('type') == 'step':
            row['decisions'] += 1
            if action in {'test_tool', 'run_tool'}:
                trial_events.append({'step': event['step'], 'action': action,
                    'name': event.get('arguments', {}).get('name'), 'ok': result.get('ok'),
                    'result': result})
            if action == 'set_daily_plan' and result.get('ok'):
                row['review_revisions'] += 1
        primitive = event.get('type') == 'tool_primitive' or (
            event.get('type') == 'step' and action and action not in {
                'set_daily_plan', 'define_tool', 'test_tool', 'run_tool', 'finish'})
        if primitive:
            primitives[action] += 1
            row['primitive_actions'] += 1
            failed = result.get('ok') is False or result.get('return_value') is False
            failed_actions += int(failed)
            row['failed_actions'] += int(failed)
    if (output / 'summary.json').exists():
        summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    completed = bool((summary or {}).get('evaluation', {}).get('completed'))
    tools = json.loads((output / 'tools.json').read_text(encoding='utf-8')) if (output / 'tools.json').exists() else {}
    learning = json.loads((output / 'learning.json').read_text(encoding='utf-8')) if (output / 'learning.json').exists() else {}
    latest_reviews = {tuple(r['date']):r for r in learning.get('reviews', [])}
    prediction_coverage = []
    for date, review in latest_reviews.items():
        activities = review.get('review', {}).get('activities', [])
        prediction_coverage.append({'date':list(date), 'activities':len(activities),
            'net_gold_estimates':sum(x.get('net_gold_estimate') is not None for x in activities),
            'active_time_estimates':sum(x.get('active_minutes_estimate') is not None for x in activities),
            'payback_estimates':sum(x.get('payback_days_estimate') is not None for x in activities),
            'statuses':dict(Counter(x.get('status') for x in activities))})
    assistance = json.loads((output / 'assistance_summary.json').read_text(encoding='utf-8')) if (output / 'assistance_summary.json').exists() else None
    if assistance is None:
        count = sum(json.loads(line).get('type') == 'completed' for line in
                    (output / 'interventions.jsonl').open(encoding='utf-8')) if (output / 'interventions.jsonl').exists() else 0
        assistance = {'intervention_count': count, 'autonomous': count == 0}
    cost_summaries = [summary or {}]
    if recovery:
        source_summary = json.loads((Path(recovery['source']) / 'summary.json').read_text(encoding='utf-8'))
        cost_summaries.insert(0, source_summary)
        assistance['intervention_count'] += source_summary.get('intervention_count', 0)
        assistance['autonomous'] = False
        old_assistance = json.loads((Path(recovery['source']) / 'assistance_summary.json').read_text(encoding='utf-8'))
        assistance['actions'] = [dict(action, source_segment=Path(recovery['source']).name)
                                 for action in old_assistance.get('actions', [])] + [
                                 dict(action, source_segment=output.name) for action in assistance.get('actions', [])]
    assistance['retained_path_movement_commands'] = retained_interventions
    all_segment_cost = {key:sum(d.get(key,0) for d in cost_summaries) for key in [
        'llm_calls','prompt_tokens','completion_tokens','primitive_calls','api_errors','invalid_decisions',
        'tool_definitions','tool_trials','tool_uses','daily_plan_calls','elapsed_seconds']}
    all_segment_cost['total_tokens'] = all_segment_cost['prompt_tokens'] + all_segment_cost['completion_tokens']
    initial_cash = initial['player']['money'] if initial else None
    final_cash = final['player']['money'] if final else None
    data = {'run_id': output.name, 'analyzed_at': datetime.now(timezone.utc).isoformat(),
        'model': config['model'], 'strategy': config['strategy'], 'requested_days': config.get('profit_days'),
        'completed': completed, 'stop_reason': (summary or {}).get('stop_reason', 'still_running_or_unfinalized'),
        'initial_cash': initial_cash, 'last_cash': final_cash,
        'observed_cash_change': final_cash-initial_cash if final else None,
        'cash_score_is_terminal': completed, 'evaluation': (summary or {}).get('evaluation'),
        'elapsed_seconds': (summary or {}).get('elapsed_seconds'), 'usage': dict(usage),
        'usage_meaning':'Retained outcome path only; all_segment_cost also includes discarded crash/recovery work.',
        'all_segment_cost':all_segment_cost, 'recovery':recovery,
        'decisions': dict(decisions), 'primitive_actions': dict(primitives),
        'invalid_decisions': invalid, 'failed_primitive_results': failed_actions,
        'daily': list(daily.values()), 'crop_observations':list(crop_observations.values()),
        'economic_prediction_coverage': prediction_coverage,
        'review_revisions': len(learning.get('reviews', [])),
        'distinct_review_dates': len({tuple(r['date']) for r in learning.get('reviews', [])}),
        'skills': tools, 'skill_execution_events': trial_events, 'assistance': assistance,
        'trajectory_events': events,
        'limitations': ['One development episode cannot estimate a treatment effect.',
            'Initial farm assets are supplied, not acquired by the agent.',
            'Skill postcondition passes and model economic beliefs are not independent semantic validation.',
            'An unfinished run has no full-horizon cash score.']}
    (output / 'analysis.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    text = [f'# Agent C ten-day experiment {output.name}', '',
        f"Model: `{config['model']}`. Completed: **{completed}**. Stop reason: `{data['stop_reason']}`.", '',
        f"Observed cash: **{initial_cash} to {final_cash}** (change {data['observed_cash_change']:+d} gold). "
        + ('This is the completed horizon cash score.' if completed else 'This is an interim balance, not a completed horizon score.'), '',
        f"All-segment prompt tokens: {all_segment_cost['prompt_tokens']:,}; completion tokens: {all_segment_cost['completion_tokens']:,}. "
        f"All-segment model calls: {all_segment_cost['llm_calls']:,}. These include discarded work before recovery. "
        f"Retained-path responses with usage: {usage['responses_with_usage']:,}.", '',
        f"Logged external movement interventions: **{assistance['intervention_count']}**. "
        f"Retained-path movement commands: {retained_interventions}. "
        f"Distinct dates with reviews: {data['distinct_review_dates']}; review revisions: {data['review_revisions']}.", '',
        '## Daily observations', '',
        '| Date | First cash | Last cash | First time | Last time | Decisions | Primitive actions | Review revisions |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in daily.values():
        text.append('| '+' | '.join(str(r[k]) for k in ['date','first_cash','last_cash','first_time','last_time','decisions','primitive_actions','review_revisions'])+' |')
    text += ['', '## Final skill library', '', '| Skill | Revision | Trials | Reuses | Passes | Failures | Verified |',
             '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for name, tool in tools.items():
        text.append('| '+name+' | '+' | '.join(str(tool.get(k)) for k in ['revision','trials','uses','passes','failures','verified'])+' |')
    text += ['', '## Interpretation limits', ''] + ['- '+x for x in data['limitations']]
    text += ['', 'All candidate definitions, revisions, results, and review text remain in the archived trajectory. '
             'The final library counters alone omit replaced revisions. The model request/response archive excludes credentials.', '']
    (output / 'report_notes.md').write_text('\n'.join(text), encoding='utf-8')
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    d=analyze(args.output.resolve())
    print(json.dumps({k:d[k] for k in ['run_id','completed','stop_reason','initial_cash','last_cash','usage','assistance']},ensure_ascii=False))


if __name__=='__main__':
    main()
