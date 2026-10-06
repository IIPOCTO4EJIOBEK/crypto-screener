import pytest
from dataclasses import asdict
from src.trade.capital_flow import performance,top_up
from src.trade.ledger import Ledger
from src.trade.intraday import BotState
from src.trade.atomic_store import transaction,read_state
def test_deposit_is_not_profit_and_preserves_predeposit_drawdown():
 rows=[{'kind':'equity','equity':900},{'kind':'capital_flow','amount':9000,'equity_before':900},{'kind':'equity','equity':9900}]
 p=performance(1000,rows,9900)
 assert p['pnl']==-100 and p['funded_capital']==10000
 assert p['return_fraction']==pytest.approx(-.1)
 assert p['drawdown_fraction']==pytest.approx(-.1)
def test_multiple_deposits_and_subsequent_profit_are_unitised():
 rows=[{'kind':'capital_flow','amount':1000,'equity_before':1100},{'kind':'equity','equity':2310},{'kind':'capital_flow','amount':500,'equity_before':2310}]
 assert performance(1000,rows,2810)['return_fraction']==pytest.approx(.21)
def test_topup_preserves_positions_history_and_is_idempotent(tmp_path):
 ledger=Ledger(tmp_path/'screener-test');st=BotState(cash=980,peak=1050,start_equity=1000,day_pnl=-20,pending_funding=2)
 with transaction(ledger) as tx:ledger.log('sentinel');tx.commit(st)
 result=top_up(ledger,10000,'test');raw=read_state(ledger.root)
 assert result['amount']==9000 and raw['cash']==9980 and raw['peak']==10050
 assert raw['start_equity']==1000 and raw['day_pnl']==-20 and raw['pending_funding']==2
 assert ledger.journal()[0]['kind']=='sentinel'
 assert top_up(ledger,10000,'test') is None
 assert top_up(ledger,10000,'another-id') is None
 assert len([r for r in ledger.journal() if r['kind']=='capital_flow'])==1
