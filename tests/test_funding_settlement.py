import json,sqlite3
import pytest
from pathlib import Path
from src.trade.atomic_store import transaction,database,profile_lock
from src.trade.intraday import Config,cycle,load_state,msk_day
from src.trade.ledger import Ledger
from src.trade.funding_settlement import pending,process,settle,amount
from tests.test_fast_monitor import initial,position,Broker

def close_pending(root):
 state=initial();state.put(position());ledger=Ledger(root);broker=Broker();broker.defer_funding=True
 def forbidden(*args):raise AssertionError('funding called in exit transaction')
 broker.funding=forbidden
 with transaction(ledger) as tx:
  result=cycle([],state,broker=broker,ledger=ledger,candles=lambda *a:[],now_ms=20000,cfg=Config(no_timeout=True,funding=True),live_prices={'AAAUSDT':(97,19900)})
  tx.commit(state,result['events'],notify=True)
 assert result['closed']==1 and not state.positions and state.pending_funding==1
 return state,ledger

def test_stop_does_not_wait_for_funding_and_failure_keeps_pending(tmp_path):
 state,ledger=close_pending(tmp_path);cash=state.cash
 def failing(*args):
  with profile_lock(tmp_path):pass
  raise RuntimeError('offline')
 with pytest.raises(RuntimeError):process(tmp_path,failing,now_ms=90000)
 assert len(pending(tmp_path))==1 and load_state(ledger.state_path,0,0).cash==cash
 assert ledger.journal()[0]['funding_pending']

def test_settlement_is_exactly_once_and_corrects_sql_json_pnl(tmp_path):
 state,ledger=close_pending(tmp_path);cash=state.cash
 def fetch(*args):
  with profile_lock(tmp_path):pass
  return [(11000,.001)]
 assert process(tmp_path,fetch,now_ms=90000)==1
 after=load_state(ledger.state_path,0,0)
 assert after.cash==pytest.approx(cash-.1) and after.pending_funding==0
 row=ledger.journal()[0];assert row['funding']==pytest.approx(.1) and not row['funding_pending']
 assert row['r_net']==pytest.approx((row['pnl']-row['fee']-.1)/row['funding_risk_quote'])
 assert process(tmp_path,fetch,now_ms=100000)==0
 assert not settle(tmp_path,1,.1,100000)
 assert load_state(ledger.state_path,0,0).cash==after.cash
 assert json.loads(ledger.journal_path.read_text().splitlines()[0])==row

def test_partial_quantity_and_short_credit_and_open_boundary():
 row=dict(symbol='AAAUSDT',side='short',entry=100,opened_ms=10000,exit_ts=30000,funding_legs=[dict(qty=.5,end_ms=20000),dict(qty=.5,end_ms=30000)])
 assert amount(row,lambda *a:[(10000,.5),(15000,.001),(25000,.002),(31000,.5)])==pytest.approx(-.2)

def test_correction_can_halt_new_entries_on_drawdown(tmp_path):
 state,ledger=close_pending(tmp_path)
 assert settle(tmp_path,1,400,90000)
 assert ledger.halted and load_state(ledger.state_path,0,0).peak==1000

def test_settlement_failure_rolls_back_cash_and_journal(tmp_path,monkeypatch):
 import src.trade.atomic_store as atomic
 state,ledger=close_pending(tmp_path);original=atomic.Transaction.commit
 monkeypatch.setattr(atomic.Transaction,'commit',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('abort')))
 with pytest.raises(RuntimeError):settle(tmp_path,1,.1,90000)
 assert load_state(ledger.state_path,0,0).cash==state.cash and pending(tmp_path)
 monkeypatch.setattr(atomic.Transaction,'commit',original)
 assert settle(tmp_path,1,.1,90000)

def test_pending_funding_blocks_entries_but_never_exits(tmp_path):
 state,ledger=close_pending(tmp_path)
 from tests.test_screener_bot import row
 result=cycle([row()],state,broker=Broker(),ledger=ledger,candles=lambda *a:[],now_ms=90000,cfg=Config(no_timeout=True))
 assert result['opened']==0

def test_funding_grace_never_fetches_early(tmp_path):
 close_pending(tmp_path)
 assert process(tmp_path,lambda *a:(_ for _ in ()).throw(AssertionError('early REST')),now_ms=30000)==0

def test_committed_settlement_survives_projection_crash_without_double_charge(tmp_path,monkeypatch):
 import src.trade.atomic_store as atomic
 state,ledger=close_pending(tmp_path)
 monkeypatch.setattr(atomic,'replace_json',lambda *a:(_ for _ in ()).throw(OSError('disk')))
 with pytest.raises(OSError):settle(tmp_path,1,.1,90000)
 after=load_state(ledger.state_path,0,0)
 assert after.cash==pytest.approx(state.cash-.1) and not after.pending_funding
 assert not pending(tmp_path) and not settle(tmp_path,1,.1,100000)

def test_late_funding_never_changes_next_days_day_pnl(tmp_path):
 state,ledger=close_pending(tmp_path)
 with transaction(ledger) as tx:
  state.day=msk_day(20000)+1;state.day_pnl=7;tx.commit(state)
 assert settle(tmp_path,1,.1,86_490_000)
 assert load_state(ledger.state_path,0,0).day_pnl==7
