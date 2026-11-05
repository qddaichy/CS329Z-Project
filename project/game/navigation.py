"""Verify ordinary movement and bound repeated attempts without teleporting."""
from collections import Counter

VERSION = 2


def point(value):
    return (value['x'], value['y']) if isinstance(value, dict) else tuple(value)


class NavigationGuard:
    def __init__(self):
        self.origin = None
        self.failed_attempts = 0
        self.since_map = 0
        self.targets = Counter()
        self.exits = []
        self.stalled = False

    def key(self, state):
        player = state['player']
        return (state.get('navigation', {}).get('location_id', player['location']), point(player['position']))

    def sync(self, state):
        key = self.key(state)
        if key != self.origin:
            self.origin = key
            self.failed_attempts = self.since_map = 0
            self.targets.clear()
            self.exits = []
            self.stalled = False

    def map_observed(self, data):
        current = data.get('current_location', data.get('location'))
        locations = data.get('locations', [])
        matches = [x for x in locations if x.get('name') == current]
        self.exits = data.get('exits', matches[0].get('exits', []) if matches else [])
        self.since_map = 0
        self.targets.clear()

    def before(self, state, target):
        self.sync(state)
        if self.since_map >= 2:
            self.failed_attempts += 1
            self.stalled = self.failed_attempts >= 8
            return {'ok': False, 'status': 'navigation_observation_required',
                    'error': 'Two moves failed here. Call observe_local_map or observe_location_details before another movement attempt.',
                    'known_exits': self.exits, **self.status()}
        return None

    def after(self, before, after, target, reply):
        old, new = self.key(before), self.key(after)
        warped = old[0] != new[0]
        moved = old != new
        reached = warped or (new[1] == tuple(target) and moved)
        if moved:
            self.sync(after)
        else:
            self.failed_attempts += 1
            self.since_map += 1
            self.targets[tuple(target)] += 1
            self.stalled = self.failed_attempts >= 8
        status = 'warped' if warped else 'reached' if reached else 'partial' if moved else 'blocked'
        return {'ok': reached, 'status': status, 'return_value': reply,
                'requested_target': list(target), 'observed_position': list(new[1]),
                'location_changed': warped, 'position_changed': moved,
                'note': 'Position verified from game state. Partial moves require replanning; use observed exit approach_position and step_direction for boundary exits.',
                **self.status()}

    def status(self):
        return {'version': VERSION, 'failed_moves_at_position': self.failed_attempts,
                'map_observation_required': self.since_map >= 2, 'stalled': self.stalled}
