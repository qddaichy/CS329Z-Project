"""Verify loaded interior exits and movement truthfulness without an LLM.

Load a disposable fixture with prepare_profit_test first. This uses ordinary
movement/interactions only, writes all observations, and leaves the game paused.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from project.agents.react import GameTools, tile_position
from project.game.actions import ActionProxy
from project.paths import REPO_ROOT


def main():
    output=REPO_ROOT/'.tools/navigation-smoke'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True)
    proxy=ActionProxy(10783);game=GameTools(proxy)
    report={'model_calls':0,'steps':[],'passed':False}
    def act(name,args):
        proxy.resume_game()
        result=game.execute(name,args)
        time.sleep(.7)
        proxy.pause_game()
        state=game.observe()
        report['steps'].append({'action':name,'arguments':args,'result':result,'state':state})
        print(name,args,result.get('status',result.get('ok')),state['player']['location'],state['player']['position'],flush=True)
        return result,state
    def move_to(position):
        state=game.last_state or game.observe();x,y=tile_position(state['player']['position'])
        if (x,y)==tuple(position):return state
        result,state=act('move',{'x':position[0]-x,'y':position[1]-y})
        if not result['ok']:raise AssertionError(result)
        return state
    try:
        proxy.pause_game();initial=game.observe();report['initial']=initial
        if initial['player']['location']!='Farm':raise RuntimeError('Load the disposable farm fixture first.')
        world=game.execute('observe_local_map',{})['data'];farm=world['locations'][0]
        for kind in ['Deluxe Barn','Deluxe Coop']:
            building=next(x for x in farm['buildings'] if x['type']==kind)
            x,y=building['human_door'];move_to((x,y+1))
            _,state=act('interact',{'direction':0})
            assert state['player']['location']==kind
            data=game.execute('observe_local_map',{})['data']
            assert len(data['locations'])==1,'Current interior missing from map'
            details=game.execute('observe_location_details',{})['data']
            assert details['exits'],'Interior detail must expose actual warps'
            exits=data['locations'][0]['exits'];report['steps'].append({'map':data,'details':details})
            exit=next(x for x in exits if x['target']=='Farm')
            state=move_to(exit['approach_position'])
            if exit.get('step_direction') is not None:
                _,state=act('move_step',{'direction':exit['step_direction']})
            assert state['player']['location']=='Farm',exit
            assert not state['navigation']['controller_attached'],'Stale path survived the warp'
            assert not state['navigation']['api_path_active'],'Completed request survived the warp'
        # Reject an out-of-map target without silently moving to another target.
        before=tile_position(game.last_state['player']['position'])
        result,state=act('move',{'x':-64,'y':-64})
        assert not result['ok'] and result['status']=='blocked'
        assert tile_position(state['player']['position'])==before
        report['passed']=True
    except Exception as error:
        report['error']=repr(error)
        raise
    finally:
        proxy.pause_game()
        (output/'result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print('Report:',output/'result.json',flush=True)


if __name__=='__main__':main()
