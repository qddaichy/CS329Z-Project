import json
import unittest
from unittest.mock import Mock, patch

from project.agents.crop_reference import lookup_crop_reference
from project.agents.hybrid import HybridAgent
from project.agents.hybrid_reflection import ReflectionHybridAgent
from project.agents.hybrid_economics import EconomicsHybridAgent
from project.agents.react import GameTools, validate_call, run_episode
from project.checks.check_react_baseline import response
from project.checks.check_hybrid_variations import observed, plan


class CropReferenceChecks(unittest.TestCase):
    def test_known_crop_and_seed_lookup(self):
        result = lookup_crop_reference('Parsnip Seeds', 'spring')['data']
        self.assertEqual(result['count'], 1)
        crop = result['crops'][0]
        self.assertEqual((crop['base_unit_sell_price'], crop['initial_growth_days'], crop['regrow_days']), (35, 4, None))
        self.assertEqual(lookup_crop_reference('Parsnip', 'fall')['data']['count'], 0)
        self.assertEqual(lookup_crop_reference('Strawberry', 'all')['data']['crops'][0]['regrow_days'], 4)
        self.assertEqual(lookup_crop_reference('', 'all')['data']['count'], 50)
        self.assertEqual(lookup_crop_reference('does not exist', 'all')['data']['count'], 0)
        with self.assertRaises(ValueError): lookup_crop_reference('', 'never')

    def test_all_variants_have_the_same_tool_and_game_mechanics(self):
        agents = [kind(Mock(), 'offline') for kind in (HybridAgent, ReflectionHybridAgent, EconomicsHybridAgent)]
        schemas = []
        for agent in agents:
            schemas.append(next(s for s in agent.available_tool_schemas() if s['function']['name'] == 'lookup_crop_reference'))
            self.assertIn('lookup_crop_reference', agent.system_prompt())
            self.assertIn('reusable feeding', agent.system_prompt())
        self.assertEqual(schemas[0], schemas[1])
        self.assertEqual(schemas[1], schemas[2])
        _, args = validate_call('lookup_crop_reference', json.dumps({'reason': 'compare', 'name': 'Kale', 'season': 'spring'}))
        self.assertEqual(GameTools(Mock()).execute('lookup_crop_reference', args)['data']['crops'][0]['base_unit_sell_price'], 110)

    def test_lookup_does_not_mutate_shared_reference(self):
        first = lookup_crop_reference('Parsnip', 'all')
        first['data']['crops'][0]['base_unit_sell_price'] = 999
        self.assertEqual(lookup_crop_reference('Parsnip', 'all')['data']['crops'][0]['base_unit_sell_price'], 35)

    def test_query_runs_through_agent_loop(self):
        client, proxy = Mock(), Mock()
        client.chat.completions.create.side_effect = [
            response(('lookup_crop_reference', {'reason': 'Check crop', 'name': 'Parsnip', 'season': 'spring'})),
            response(('finish', {'reason': 'End offline check'}))]
        proxy.observe_state.return_value = json.dumps(observed())
        events = []
        with patch('project.agents.react.time.sleep'):
            run_episode(HybridAgent(client, 'offline'), GameTools(proxy), 'test', max_steps=3, emit=events.append)
        result = next(e['result'] for e in events if e.get('type') == 'step' and e.get('action') == 'lookup_crop_reference')
        self.assertEqual(result['data']['crops'][0]['initial_growth_days'], 4)


if __name__ == '__main__': unittest.main()
