"""Daily activity estimates and gates without next-morning cash predictions."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from project.agents.hybrid_economics import EconomicsHybridAgent
from project.agents.react import GameTools, run_episode
from project.agents.daily_experience import DailyExperience
from project.checks.check_hybrid_variations import observed, plan, economic_review
from project.checks.check_react_baseline import response
from project.checks.check_hybrid import definition


class DailyEconomicsChecks(unittest.TestCase):
    def test_agent_c_retains_both_general_reflection_and_economic_beliefs(self):
        agent = EconomicsHybridAgent(Mock(), 'offline')
        fields = agent.extra_tool_specs()['set_daily_plan'][1]
        self.assertIn('daily_reflection', fields)
        self.assertIn('skill_review', fields)
        self.assertIn('economics_json', fields)
        self.assertIn('Results (observed outcomes)', agent.system_prompt())
        values = plan(economics_json=json.dumps(economic_review()))
        incomplete = {k: v for k, v in values.items() if k != 'daily_reflection'}
        with self.assertRaises(ValueError): agent.set_daily_plan(incomplete, observed())
        self.assertEqual((agent.daily_plans, agent.reviews, agent.general_reflections), ([], [], []))
        result = agent.set_daily_plan(values, observed())
        self.assertEqual(result['daily_reflection']['review'], values['daily_reflection'])
        self.assertEqual(agent.learning_snapshot()['general_reflections'][0]['review'], values['daily_reflection'])
        self.assertEqual(agent.reviews[0]['review']['activities'][0]['status'], 'hypothesis')
        agent.update_economic_knowledge({'economics_json': values['economics_json']}, observed())
        self.assertEqual(len(agent.general_reflections), 1)
        self.assertEqual(len(agent.reviews), 2)

    def test_all_null_and_missing_activity_estimates_are_rejected_atomically(self):
        agent = EconomicsHybridAgent(Mock(), 'offline')
        for review in [economic_review(net_gold_estimate=None, active_minutes_estimate=None),
                       {k:v for k,v in economic_review().items() if k != 'activities'}]:
            with self.assertRaises(ValueError):
                agent.set_daily_plan(plan(economics_json=json.dumps(review)), observed())
        self.assertEqual(agent.reviews, [])
        self.assertEqual(agent.daily_plans, [])

    def test_activity_estimates_and_uncertainty_remain_validated(self):
        agent = EconomicsHybridAgent(Mock(), 'offline')
        for changes in [{'net_gold_estimate':True}, {'net_gold_estimate':float('nan')},
                        {'active_minutes_estimate':-1}, {'active_minutes_estimate':float('inf')},
                        {'uncertainty':''}, {'status':'supported'}]:
            review=economic_review(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                agent.validate_review(json.dumps(review))
        self.assertEqual(agent.daily_plans, [])

    def test_forecast_free_review_unlocks_today_and_legacy_reviews_need_refresh(self):
        agent=EconomicsHybridAgent(Mock(),'offline')
        accepted=agent.set_daily_plan(plan(economics_json=json.dumps(economic_review())),observed())
        self.assertEqual(set(accepted['review']['review']),{'activities','allocation_plan'})
        self.assertEqual(accepted['review']['version'],4)
        self.assertNotIn('forecast_context',accepted['review'])
        self.assertFalse(agent.requires_daily_plan(observed()))
        self.assertTrue(agent.requires_daily_plan(observed(2)))
        # A legacy same-day plan cannot bypass the new numeric contract.
        agent.reviews[-1]['version']=2
        self.assertTrue(agent.requires_daily_plan(observed()))

    def test_new_day_preserves_revisions_without_cash_scoring(self):
        agent=EconomicsHybridAgent(Mock(),'offline')
        agent.record_event({'type':'initial_observation','state':observed()})
        review=economic_review()
        agent.set_daily_plan(plan(economics_json=json.dumps(review)),observed())
        review['activities'][0]['net_gold_estimate']=30
        agent.set_daily_plan(plan(economics_json=json.dumps(review)),observed())
        before=copy.deepcopy(agent.reviews)
        agent.record_event({'type':'step','action':'wait','result':{'ok':True},'state':observed(2,620)})
        agent.record_event({'type':'step','action':'wait','result':{'ok':True},'state':observed(2,700)})
        self.assertEqual(agent.reviews,before)
        self.assertNotIn('forecast_evaluations',agent.learning_snapshot())
        self.assertNotIn('forecast_context',agent.reviews[0])

    def test_intraday_update_retains_prices_without_replacing_plan(self):
        agent = EconomicsHybridAgent(Mock(), 'offline')
        s = observed()
        agent.record_event({'type': 'initial_observation', 'state': s})
        agent.set_daily_plan(plan(economics_json=json.dumps(economic_review())), s)
        agent.record_event({'type': 'step', 'action': 'wait', 'arguments': {'duration_ms': 50},
                            'result': {'ok': True}, 'state': s})
        ref = next(iter(agent.experience.evidence_ids))
        review = economic_review()
        review['item_prices'] = [{'item': 'Parsnip', 'unit_price': 35, 'quality': 'normal',
                                  'sale_channel': 'shop', 'status': 'supported',
                                  'evidence_ids': [ref], 'uncertainty': 'only this sale channel observed'}]
        result = agent.update_economic_knowledge({'economics_json': json.dumps(review)}, s)
        self.assertEqual(result['review']['revision'], 2)
        self.assertEqual(agent.learning_snapshot()['reviews'][-1]['review']['item_prices'][0]['unit_price'], 35)
        self.assertEqual(len(agent.daily_plans), 1)
        self.assertFalse(agent.requires_daily_plan(s))
        self.assertTrue(agent.requires_daily_plan(observed(2)))
        with self.assertRaises(ValueError):
            agent.update_economic_knowledge({'economics_json': json.dumps(review)}, observed(2))

    def test_price_validation_rejects_unsupported_and_invalid_prices(self):
        agent = EconomicsHybridAgent(Mock(), 'offline')
        review = economic_review()
        entry = {'item': 'Parsnip', 'unit_price': 35, 'quality': 'normal', 'sale_channel': 'shop',
                 'status': 'hypothesis', 'evidence_ids': [], 'uncertainty': 'unconfirmed'}
        for change in ({'unit_price': -1}, {'unit_price': True}, {'status': 'supported'},
                       {'evidence_ids': ['e999']}, {'status': 'unknown'}):
            review['item_prices'] = [{**entry, **change}]
            with self.subTest(change=change), self.assertRaises(ValueError):
                agent.validate_review(json.dumps(review))

    def test_intraday_update_executes_via_runner_and_appears_in_dashboard(self):
        from project.tools.log_viewer import dashboard_data
        review = economic_review()
        review['item_prices'] = [{'item': 'Parsnip', 'unit_price': None, 'quality': 'unknown',
                                  'sale_channel': 'unknown', 'status': 'unknown',
                                  'evidence_ids': [], 'uncertainty': 'not observed'}]
        client, proxy = Mock(), Mock()
        client.chat.completions.create.side_effect = [
            response(('set_daily_plan', {'reason': 'Plan', **plan(economics_json=json.dumps(economic_review()))})),
            response(('update_economic_knowledge', {'reason': 'Record price uncertainty', 'economics_json': json.dumps(review)})),
            response(('finish', {'reason': 'Stop offline check'}))]
        proxy.observe_state.return_value = json.dumps(observed())
        agent = EconomicsHybridAgent(client, 'offline')
        events = []
        with patch('project.agents.react.time.sleep'):
            run_episode(agent, GameTools(proxy), 'Earn', max_steps=3, emit=events.append)
        update = next(event for event in events if event.get('type') == 'step' and event.get('action') == 'update_economic_knowledge')
        self.assertTrue(update['result']['ok'])
        self.assertEqual(len(agent.daily_plans), 1)
        rows = dashboard_data({'learning': {'latest_review': agent.reviews[-1]}})['economy_rows']
        self.assertTrue(any(row['values'][0] == 'Price: Parsnip' for row in rows))

    def test_prompt_schema_has_no_cash_prediction_and_rejects_obsolete_fields(self):
        from project.agents.hybrid_economics import ECONOMICS_PROMPT
        agent=EconomicsHybridAgent(Mock(),'offline')
        example,_=json.JSONDecoder().raw_decode(ECONOMICS_PROMPT.split('Format: ',1)[1])
        agent.validate_review(json.dumps(example))
        self.assertNotIn('daily_forecast',agent.system_prompt())
        self.assertNotIn('next-morning',agent.review_description)
        review=economic_review();review['daily_forecast']={'next_morning_cash_estimate':600}
        with self.assertRaises(ValueError):agent.validate_review(json.dumps(review))

    def test_three_rejected_reviews_stop_without_gameplay_or_fourth_model_call(self):
        client=Mock();bad=economic_review(net_gold_estimate=None,active_minutes_estimate=None)
        client.chat.completions.create.side_effect=[response(('set_daily_plan',{
            'reason':'Plan',**plan(economics_json=json.dumps(bad))})) for _ in range(3)]
        proxy=Mock();proxy.observe_state.return_value=json.dumps(observed())
        agent=EconomicsHybridAgent(client,'offline');events=[]
        with patch('project.agents.react.time.sleep'):
            result=run_episode(agent,GameTools(proxy),'Earn',max_steps=10,emit=events.append)
        self.assertEqual(result['stop_reason'],'daily_review_failed')
        self.assertEqual(result['llm_calls'],3)
        self.assertEqual(result['primitive_calls'],0)
        self.assertEqual(len(agent.review_rejections),3)
        proxy.resume_game.assert_not_called()
        self.assertEqual(agent.reviews,[])

    def test_date_change_requires_new_estimates_before_more_gameplay(self):
        client=Mock();review=json.dumps(economic_review())
        client.chat.completions.create.side_effect=[
            response(('set_daily_plan',{'reason':'First day',**plan(economics_json=review)})),
            response(('wait',{'reason':'Observe settlement','duration_ms':50})),
            response(('wait',{'reason':'Attempt to skip review','duration_ms':50})),
            response(('set_daily_plan',{'reason':'Second day',**plan(economics_json=review)})),
            response(('finish',{'reason':'Offline test complete'})),
        ]
        proxy=Mock();proxy.observe_state.side_effect=[json.dumps(x) for x in
            [observed(),observed(),observed(2,620),observed(2,620),observed(2,620)]]
        agent=EconomicsHybridAgent(client,'offline')
        with patch('project.agents.react.time.sleep'):
            result=run_episode(agent,GameTools(proxy),'Earn',max_steps=5)
        self.assertEqual(result['primitive_calls'],1)
        self.assertEqual(len(agent.reviews),2)
        self.assertEqual(len(agent.review_rejections),1)
        self.assertNotIn('forecast_evaluations',result['learning'])
        self.assertFalse(agent.requires_daily_plan(observed(2,620)))

    def test_shared_facts_preserve_cash_flow_and_observed_time(self):
        ledger=DailyExperience();s=observed();ledger.record({'type':'initial_observation','state':s})
        for cash,clock,day in [(475,640,1),(575,700,1),(635,600,2)]:
            s=observed(day,cash);s['gamestate']['time']=clock
            ledger.record({'type':'step','action':'wait','result':{'ok':True},'state':s})
        facts=ledger.context(True)['days'][0]['facts']
        self.assertEqual((facts['cash_increases'],facts['cash_decreases'],facts['net_observed_cash_change']),(160,25,135))
        self.assertEqual(facts['same_day_observed_game_minutes'],60)
        self.assertEqual(len(facts['cash_event_refs']),3)

    def test_recipe_cannot_execute_more_steps_after_day_changes(self):
        agent=EconomicsHybridAgent(Mock(),'offline')
        agent.set_daily_plan(plan(economics_json=json.dumps(economic_review())),observed())
        agent.define_tool(definition(steps=[{'tool':'wait','arguments':{'duration_ms':50}}]*2))
        execute=Mock(return_value=({'ok':True},observed(2,620)))
        result=agent.run_tool({'name':'step_east','arguments_json':'{"direction":2}'},
                              observed(),execute,trial=True,completed=lambda:False)
        self.assertTrue(result['daily_plan_required'])
        self.assertEqual(execute.call_count,1)
        self.assertEqual(agent.library['step_east']['last_status'],'interrupted')
        self.assertEqual(agent.library['step_east']['failures'],0)
