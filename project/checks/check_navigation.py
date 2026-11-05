"""Movement acknowledgement is not success; never silently retarget or teleport."""
import json
import unittest
from unittest.mock import Mock, patch
from project.game.actions import ActionProxy
from project.agents.react import GameTools, ReActAgent, run_episode
from project.checks.check_react_baseline import state


class NavigationChecks(unittest.TestCase):
    def game(self, observations, reply=True):
        proxy=Mock();proxy.move.return_value=reply;proxy.move_step.return_value=reply
        proxy.observe_state.side_effect=[json.dumps(s) for s in observations]
        return GameTools(proxy),proxy

    def test_false_ack_and_no_displacement_cannot_pass(self):
        for reply in [True,False,None]:
            game,proxy=self.game([state(),state()],reply)
            result=game.execute('move',{'x':1,'y':0})
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'],'blocked')
            proxy.move.assert_called_once_with(x=1,y=0)

    def test_partial_move_reports_actual_position_and_warp_is_success(self):
        game,_=self.game([state(),state(position=(1,0))])
        result=game.execute('move',{'x':4,'y':0})
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['observed_position'],[1,0])
        self.assertFalse(result['ok'])
        game,_=self.game([state(),state(position=(11,14),location='Barn')])
        result=game.execute('move',{'x':1,'y':0})
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'],'warped')

    def test_two_failures_require_map_before_more_moves(self):
        game,proxy=self.game([state(),state(),state(),state(position=(1,0))])
        for _ in range(2):self.assertFalse(game.execute('move',{'x':1,'y':0})['ok'])
        self.assertEqual(game.execute('move',{'x':1,'y':0})['status'],'navigation_observation_required')
        self.assertEqual(proxy.move.call_count,2)
        proxy.observe_world_map.return_value=json.dumps({'available':True,'current_location':'Farm',
            'locations':[{'name':'Farm','exits':[{'position':[1,0],'target':'Town'}]}]})
        game.execute('observe_local_map',{})
        self.assertTrue(game.execute('move',{'x':1,'y':0})['ok'])
        self.assertEqual(game.navigation.failed_attempts,0)

    def test_eight_failures_stop_and_keep_game_paused(self):
        proxy=Mock();proxy.observe_state.return_value=json.dumps(state());proxy.move.return_value=False
        game=GameTools(proxy)
        for i in range(8):
            if i and i%2==0:game.navigation.map_observed({})
            game.execute('move',{'x':1,'y':0})
        self.assertTrue(game.navigation.stalled)
        client=Mock();agent=ReActAgent(client,'offline')
        result=run_episode(agent,game,'Move',max_steps=5)
        self.assertEqual(result['stop_reason'],'navigation_stalled')
        client.chat.completions.create.assert_not_called()
        proxy.resume_game.assert_not_called()

    def test_transport_does_not_retry_offsets_and_step_returns_boolean(self):
        proxy=ActionProxy(10783)
        with patch.object(proxy,'_post_message',return_value='False') as send:
            self.assertFalse(proxy.move(20,3))
            send.assert_called_once_with('move_relative%20%3')
        with patch.object(proxy,'_post_message',return_value='False'):
            self.assertFalse(proxy.move_step(3))
        with patch.object(proxy,'_post_message',return_value='True'):
            self.assertTrue(proxy.move_step(3))
