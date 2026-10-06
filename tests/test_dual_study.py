import pytest
from tools.trade.dual_study import leg

def bar(o,h,l,c):return dict(open=o,high=h,low=l,close=c,timestamps='2026-10-01 00:00:00')
def test_equal_hedge_price_moves_cancel_before_costs():
 cs=[bar(100,100.3,99.7,100.2)]
 a=leg(cs,1,fee=0,slippage=0);b=leg(cs,-1,fee=0,slippage=0)
 assert a['pnl']+b['pnl']==pytest.approx(0)

def test_flat_market_pays_four_fees():
 cs=[bar(100,100,100,100)]
 assert leg(cs,1,slippage=0)['pnl']+leg(cs,-1,slippage=0)['pnl']==pytest.approx(-2)

def test_two_accounts_can_both_hit_stop():
 cs=[bar(100,101.5,98.5,100)]
 a=leg(cs,1,slippage=0);b=leg(cs,-1,slippage=0)
 assert a['reason']==b['reason']=='stop'
 assert a['pnl']+b['pnl']< -20

def test_no_future_price_after_stop_is_used():
 a=leg([bar(100,100,98,99),bar(99,500,1,500)],1,slippage=0)
 assert a['exit']==99 and a['reason']=='stop'
