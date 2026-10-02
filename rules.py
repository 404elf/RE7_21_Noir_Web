"""Shared room rule validation; synchronous engine scopes must never contain await."""
import copy
from contextlib import contextmanager
import re7_21 as engine

GAME = [
 ('max_hp','最大生命值','Maximum health',1,999,1,True),
 ('max_trumps_hand_size','手持王牌上限','Trump hand limit',1,100,1,True),
 ('max_active_trumps_on_table','场上王牌上限','Table trump limit',1,100,1,True),
 ('target_score','默认目标点数','Default target',1,999,1,True),
 ('initial_trumps_count','开局王牌数量（另加每局补充）','Starting trumps (+ round reward)',0,100,1,True),
 ('round_reward_trumps_count','每回合补充王牌','Round trump reward',0,100,1,True),
 ('hit_draw_trump_probability','抽牌时获得王牌概率（0–1）','Trump chance on hit (0–1)',0,1,.05,False),
 ('number_card_draw_probability','普通数字王牌概率（0–1）','Number trump chance (0–1)',0,1,.05,False),
 ('deck_range_start','牌堆最小点数','Lowest number card',1,100,1,True),
 ('deck_range_end','牌堆最大点数','Highest number card',1,100,1,True),
]

def rules_checked(data):
    if not isinstance(data,dict) or set(data)-{'game_settings','trump_weights'}: raise ValueError('invalid_rules')
    result=copy.deepcopy(engine.GAME_CONFIG)
    settings=data.get('game_settings',{})
    weights=data.get('trump_weights',{})
    if not isinstance(settings,dict) or not isinstance(weights,dict) or len(weights)>100: raise ValueError('invalid_rules')
    known={row[0]:row for row in GAME}
    for key,value in settings.items():
        if key not in known: raise ValueError('invalid_rule')
        row=known[key]
        if type(value) not in (int,float) or not row[3]<=value<=row[4] or (row[6] and int(value)!=value): raise ValueError('invalid_rule')
        result['game_settings'][key]=int(value) if row[6] else float(value)
    for key,value in weights.items():
        if not isinstance(key,str) or len(key)>48 or type(value) not in (int,float) or not 0<=value<=10000: raise ValueError('invalid_weight')
        result['trump_weights'][key]=value
    conf=result['game_settings']
    if conf['deck_range_end']-conf['deck_range_start']<3: raise ValueError('invalid_deck')
    return result

@contextmanager
def rules_active(rules):
    # Game engine operations are synchronous on this event loop; never await here.
    old=(engine.SETTINGS,engine.WEIGHTS,engine.MAX_HP,engine.MAX_TRUMPS,engine.MAX_TABLE_SLOTS)
    engine.SETTINGS,engine.WEIGHTS=rules['game_settings'],rules['trump_weights']
    engine.MAX_HP=engine.SETTINGS['max_hp'];engine.MAX_TRUMPS=engine.SETTINGS['max_trumps_hand_size'];engine.MAX_TABLE_SLOTS=engine.SETTINGS['max_active_trumps_on_table']
    try: yield
    finally: engine.SETTINGS,engine.WEIGHTS,engine.MAX_HP,engine.MAX_TRUMPS,engine.MAX_TABLE_SLOTS=old
