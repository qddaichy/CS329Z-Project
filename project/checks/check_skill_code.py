import copy
import json
import unittest
from unittest.mock import Mock

from project.agents.hybrid import HybridAgent
from project.agents.skill_code import Interpreter, SkillError, SkillStop, parse_code
from project.checks.check_react_baseline import state


def definition(code):
    return {'name': 'my_skill', 'description': 'Agent-authored behavior', 'parameters_json': '{}',
            'code': code, 'postcondition_json': json.dumps({'path': 'player.position', 'op': 'changed'})}


class CodeSkillChecks(unittest.TestCase):
    def test_agent_code_controls_sequence_and_observes_results(self):
        agent = HybridAgent(Mock(), 'offline')
        agent.define_code_tool(definition('''for i in range(2):
    result = api("move_step", {"direction": 2})
    if not result.get("ok"):
        break
'''))
        before = state()
        states = [copy.deepcopy(before), copy.deepcopy(before)]
        states[0]['player']['position'] = [1, 0]
        states[1]['player']['position'] = [2, 0]
        execute = Mock(side_effect=[({'ok': True}, s) for s in states])
        out = agent.run_tool({'name': 'my_skill', 'arguments_json': '{}'}, before, execute, True, lambda: False)
        self.assertTrue(out['postcondition_passed'])
        self.assertEqual(execute.call_count, 2)
        self.assertIn('for i', agent.library['my_skill']['code'])

    def test_disallowed_escape_and_api(self):
        for code in ['import os', 'open("x")', 'state().__class__', 'exec("pass")', 'x = [i for i in range(3)]']:
            with self.subTest(code=code), self.assertRaises(SkillError): parse_code(code)
        agent = HybridAgent(Mock(), 'offline')
        agent.define_code_tool(definition('api("set_money", {"amount": 999})'))
        execute = Mock()
        result = agent.run_tool({'name': 'my_skill', 'arguments_json': '{}'}, state(), execute, True, lambda: False)
        self.assertFalse(result['ok'])
        execute.assert_not_called()

    def test_unbounded_loop_is_stopped(self):
        interp = Interpreter({}, Mock(), fuel=60)
        with self.assertRaises(SkillStop): interp.run('while True:\n    pass')
        Interpreter({}, Mock()).run('for i in range(128):\n    pass')

    def test_visible_state_is_filtered(self):
        agent = HybridAgent(Mock(), 'offline')
        agent.define_code_tool(definition('''s = state()
if len(s["farming"]["soils"]) == 0:
    api("turn", {"direction": 1})'''))
        s = state()
        s['farming'] = {'soils': [{'position': [999, 999], 'crop': {'ready_to_harvest': True}}]}
        execute = Mock(return_value=({'ok': True}, s))
        agent.run_tool({'name': 'my_skill', 'arguments_json': '{}'}, s, execute, True, lambda: False)
        execute.assert_called_once_with('turn', {'direction': 1})

    def test_failure_stops_remaining_code(self):
        agent = HybridAgent(Mock(), 'offline')
        agent.define_code_tool(definition('for i in range(3):\n    api("turn", {"direction": 1})'))
        s = state()
        execute = Mock(return_value=({'ok': False}, s))
        out = agent.run_tool({'name': 'my_skill', 'arguments_json': '{}'}, s, execute, True, lambda: False)
        self.assertFalse(out['ok'])
        execute.assert_called_once()

if __name__ == '__main__': unittest.main()
