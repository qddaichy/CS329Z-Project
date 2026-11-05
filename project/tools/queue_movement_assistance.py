"""Queue a bounded, logged doorway movement at a safe model-decision boundary."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from project.paths import REPO_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--location', required=True)
    parser.add_argument('--x', type=int, required=True)
    parser.add_argument('--y', type=int, required=True)
    parser.add_argument('--direction', type=int, choices=range(1, 5), default=3)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    active = json.loads((REPO_ROOT / '.tools/active_supervised_run.json').read_text())
    output = Path(active['output']).resolve()
    if not output.is_relative_to(REPO_ROOT / 'runs'):
        raise ValueError('Expected an experiment directory under runs/.')
    requested = datetime.now(timezone.utc).isoformat()
    def write(value):
        temp = output / 'control.tmp'
        temp.write_text(json.dumps(value), encoding='utf-8')
        temp.replace(output / 'control.json')
    write({'pause': True, 'reason': args.reason})
    for _ in range(120):
        status = json.loads((output / 'control_status.json').read_text()) if (output / 'control_status.json').exists() else {}
        if status.get('paused') and status.get('at', '') >= requested:
            break
        time.sleep(.25)
    else:
        raise RuntimeError('Pause not acknowledged; request remains for inspection.')
    state = json.loads((output / 'checkpoint.json').read_text())['state']
    player = state['player']
    if player['location'] != args.location:
        (output / 'control.json').unlink(missing_ok=True)
        print('Location changed before assistance; resumed without moving.')
        return
    x, y = player['position']['x'], player['position']['y']
    write({'reason': args.reason, 'moves': [
        {'action': 'move', 'arguments': {'x': args.x-x, 'y': args.y-y}},
        {'action': 'move_step', 'arguments': {'direction': args.direction}}]})
    print(json.dumps({'from': [x, y], 'doorway': [args.x, args.y], 'queued_moves': 2}))


if __name__ == '__main__':
    main()
