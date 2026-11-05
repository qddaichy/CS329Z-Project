"""Shared, static crop facts; agent queries do not expose game state."""
import copy
import json
from pathlib import Path

_DATA = json.loads((Path(__file__).resolve().parent.parent / 'config' / 'crop_reference.json').read_text(encoding='utf-8'))


def lookup_crop_reference(name, season):
    query = name.strip().casefold()
    for suffix in (' seeds', ' seed'):
        if query.endswith(suffix):
            query = query[:-len(suffix)].strip()
            break
    period = season.strip().casefold()
    if period not in {'', 'all', 'spring', 'summer', 'fall', 'winter'}:
        raise ValueError('Season must be spring, summer, fall, winter or all.')
    matches = [copy.deepcopy(crop) for crop in _DATA['crops']
               if query in crop['name'].casefold() and (period in {'', 'all'} or period in crop['seasons'])]
    return {'ok': True, 'data': {'source': _DATA['source'], 'game_version': _DATA['game_version'],
            'scope': _DATA['scope'], 'query': {'name': name, 'season': season},
            'count': len(matches), 'crops': matches}}
