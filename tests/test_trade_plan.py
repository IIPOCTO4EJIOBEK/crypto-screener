from src.data.trade_plan import plan
from src.data.trade_setups import select,bot_rows
import pytest


def test_long_and_short_targets_are_three_net_r_after_costs():
 for side,stop in [('long',98),('short',102)]:
  p=plan(100,stop,side,[])
  assert p['ok'] and p['rr_net']==pytest.approx(3)
  assert abs(p['target']-100)/2>3


def test_near_confirmed_obstacle_blocks_trade_instead_of_fake_rr():
 p=plan(100,98,'long',[dict(kind='resistance',price=103,touches=2)])
 assert not p['ok'] and 'target' not in p
 # One touch is an unconfirmed reference; it does not set the final trading target.
 assert plan(100,98,'long',[dict(kind='resistance',price=103,touches=1)])['ok']


def test_target_is_before_next_confirmed_resistance():
 p=plan(100,98,'long',[dict(kind='resistance',price=110,touches=3)])
 assert p['ok'] and 106<p['target']<110


def test_page_and_paper_share_the_same_setups_and_event_keys():
 f=dict(symbol='USUSDT',tf='15m',kind='retest',title='Retest',dir='long',entry=100,stop=98,target=110,rr=5,triggered=True,plan_ok=True,age=0,exp=-.2,n=100,sig=True,ts=123,reasons=['level held'])
 d=dict(rows=[dict(symbol='USUSDT',price=101,trend={'15m':{'side':'long'}})],forms=[f,dict(f,kind='bounce',exp=-.3)])
 selected=select(d);rows=bot_rows(selected)
 assert len(rows)==1 and rows[0]['kind']=='retest' and rows[0]['direction']=='long'
 assert rows[0]['key_suffix']=='page:123'
 assert not select(dict(d,forms=[dict(f,plan_ok=False)]))


def test_candle_range_limits_target_without_inventing_three_r():
 assert not plan(100,98,'long',[],expected_move=2)['ok']
 result=plan(100,98,'long',[dict(kind='resistance',price=120,touches=2)],expected_move=9)
 assert result['ok'] and result['target']==109 and result['rr_net']>3
