import json
import pytest
from src.trade.confirmation import annotate,entry_confirmation
from src.analysis.formations import Formation
from tools.live import screen
from src.backtest.costs import Costs

@pytest.mark.parametrize('side,level,price,closed,rejected',[
 ('long',.07621,.07607,.07622,True),
 ('long',.07621,.07630,.07615,True),
 ('short',.029936,.030102,.02992,True),
 ('long',.0300,.030102,.030061,False),
 ('short',.0762,.07607,.07615,False),
])
def test_confirmed_side_must_hold_at_entry(side,level,price,closed,rejected):
 row=dict(kind='retest',direction=side,trigger_level=level,_latest_closed=closed)
 assert bool(entry_confirmation(row,price))==rejected

def test_old_retest_without_level_is_deferred():
 assert entry_confirmation(dict(kind='retest',direction='long',_requires_confirmation=True),100)

def test_confirmation_reads_only_latest_closed_candle_and_recomputes_age(tmp_path):
 row=dict(kind='retest',symbol='MUBARAKUSDT',tf='5m',ts=300000,direction='long',trigger_level=.07621)
 (tmp_path/'MUBARAKUSDT_5m.json').write_text(json.dumps([[300000,.0763,.0764,.0761,.07622,1],[600000,.07622,.0764,.0761,.07615,1],[900000,.07615,.0764,.0761,.0763,1]]))
 annotate([row],tmp_path,now=950000)
 assert row['_latest_closed']==.07615 and row['age_candles']==1
 assert entry_confirmation(row,.0763)

def test_rank_preserves_detection_time_and_exact_level(monkeypatch):
 f=Formation('retest','Ретест','long','MUBARAKUSDT','binance_futures','5m',300000,.07622,.07622,.07588,[.07755],True,.6,trigger_level=.076210123)
 monkeypatch.setattr(screen,'_books',lambda *a:{})
 result=screen.rank([f],{},None,Costs())[0]
 assert result.ts==300000 and result.trigger_level==.076210123

