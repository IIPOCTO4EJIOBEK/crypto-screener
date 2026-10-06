import json,sqlite3,time
from dataclasses import asdict
from pathlib import Path
import pytest
from src.trade.atomic_store import transaction,profile_lock,Busy,read_state,database
from src.trade.intraday import BotState,Position,Config,cycle,save_state,load_state
from src.trade.ledger import Ledger
from src.trade.notification_outbox import drain_one
from src.trade.position_controls import enqueue,pending
from src.trade.monitor_market import MinuteCache

def initial():return BotState(cash=1000,peak=1000,start_equity=1000)

def test_atomic_migration_and_rollback(tmp_path):
 ledger=Ledger(tmp_path);st=initial();save_state(ledger.state_path,st);ledger.log('open',key='old')
 with pytest.raises(RuntimeError):
  with transaction(ledger) as tx:
   st.cash=900;ledger.log('close',key='old');raise RuntimeError('crash')
 assert load_state(ledger.state_path,1,0).cash==1000
 assert len(ledger.journal())==1
 with transaction(ledger) as tx:
  st=load_state(ledger.state_path,1,0);st.cash=950;ledger.log('close',key='old');tx.commit(st,['exit'],notify=True)
  assert len(ledger.journal())==2
 assert read_state(tmp_path)['cash']==950
 assert len(ledger.journal_path.read_text().splitlines())==2

def test_committed_state_outbox_and_command_survive_projection_crash(tmp_path,monkeypatch):
 import src.trade.atomic_store as atomic
 ledger=Ledger(tmp_path);name=enqueue(tmp_path,dict(op='close',key='p',reason='manual reason'));path=tmp_path/'controls'/name
 def crash(*args):raise OSError('disk projection interrupted')
 monkeypatch.setattr(atomic,'replace_json',crash)
 with pytest.raises(OSError):
  with transaction(ledger) as tx:
   st=initial();st.cash=990;ledger.log('close',key='p');tx.commit(st,['exit'],notify=True,commands=[path])
 assert path.exists() and pending(tmp_path)==[]
 assert read_state(tmp_path)['cash']==990
 assert ledger.journal()[0]['kind']=='close'
 calls=[];assert drain_one(tmp_path,lambda text:(calls.append(text) or True,'200',0),now=1)
 assert not drain_one(tmp_path,lambda text:(True,'200',0),now=2)
 assert len(calls)==1

def test_writer_lock_and_telegram_retry(tmp_path):
 with profile_lock(tmp_path):
  with pytest.raises(Busy):
   with profile_lock(tmp_path):pass
 ledger=Ledger(tmp_path)
 with transaction(ledger) as tx:tx.commit(initial(),['event'],notify=True)
 assert drain_one(tmp_path,lambda text:(False,'429',10),now=100)
 assert not drain_one(tmp_path,lambda text:(True,'200',0),now=109)
 assert drain_one(tmp_path,lambda text:(True,'200',0),now=111)
 assert not drain_one(tmp_path,lambda text:(True,'200',0),now=500)

class Broker:
 live=False;fee=.0005

def position():return Position(key='p',symbol='AAAUSDT',kind='breakout',title='breakout',tf='5m',side='long',qty=1,entry=100,signal_entry=100,stop=98,target=104,opened_ms=10000,expires_ms=1000000,fee_in=.05,risk0=2,last_check_ms=10000)

@pytest.mark.parametrize('price,reason',[(97,'stop'),(105,'target')])
def test_realtime_exit_without_waiting_for_minute_close(tmp_path,price,reason):
 st=initial();st.put(position());ledger=Ledger(tmp_path)
 result=cycle([],st,broker=Broker(),ledger=ledger,candles=lambda *a:[],now_ms=20000,cfg=Config(no_timeout=True),live_prices={'AAAUSDT':(price,19900)})
 assert result['closed']==1 and not st.positions
 event=next(r for r in ledger.journal() if r['kind']=='close');assert event['reason']==reason
 assert event['exit']==(97 if reason=='stop' else 104)

@pytest.mark.parametrize('stamp',[1000,21000])
def test_stale_or_future_quote_cannot_close_position(tmp_path,stamp):
 st=initial();st.put(position())
 result=cycle([],st,broker=Broker(),ledger=Ledger(tmp_path),candles=lambda *a:[],now_ms=20000,cfg=Config(no_timeout=True),live_prices={'AAAUSDT':(97,stamp)})
 assert result['closed']==0 and st.positions

def test_shared_minute_cache_and_gap_recovery(tmp_path):
 now=int(time.time()*1000)//60000*60000;rows=[[now-120000,100,102,99,101,10],[now-60000,101,102,100,101,10],[now,101,102,100,101,10]]
 (tmp_path/'AAAUSDT_1m.json').write_text(json.dumps(rows))
 calls=[];cache=MinuteCache(tmp_path,lambda sym,since:calls.append(sym) or [])
 assert cache.candles('AAAUSDT',now-120000)
 assert cache.candles('AAAUSDT',now-60000) and not calls
 rows.pop(1);(tmp_path/'AAAUSDT_1m.json').write_text(json.dumps(rows));cache=MinuteCache(tmp_path,lambda sym,since:calls.append(sym) or [])
 cache.candles('AAAUSDT',now-120000);assert calls==['AAAUSDT']

def test_price_trace_catches_stop_between_monitor_passes(tmp_path):
 st=initial();st.put(position());ledger=Ledger(tmp_path)
 result=cycle([],st,broker=Broker(),ledger=ledger,candles=lambda *a:[],now_ms=20000,cfg=Config(no_timeout=True),live_prices={'AAAUSDT':(101,19900,[(15000,97),(18000,101)])})
 assert result['closed']==1
 assert next(r for r in ledger.journal() if r['kind']=='close')['exit']==97

def test_partial_take_never_reuses_earlier_minute_low(tmp_path):
 from src.data.market import Candle
 st=initial();p=position();p.tp1_r=1;p.be_after_tp1=True;st.put(p);ledger=Ledger(tmp_path)
 cycle([],st,broker=Broker(),ledger=ledger,candles=lambda *a:[],now_ms=40000,cfg=Config(no_timeout=True),live_prices={'AAAUSDT':(101,39900,[(20000,99),(30000,102),(35000,101)])})
 assert st.positions['p']['tp1_done'] and st.positions['p']['qty']==.5
 old=Candle(0,100,102,99,101,0,0,0)
 result=cycle([],st,broker=Broker(),ledger=ledger,candles=lambda *a:[old],now_ms=65000,cfg=Config(no_timeout=True),live_prices={'AAAUSDT':(101,64900,[(20000,99),(30000,102),(35000,101),(62000,101)])})
 assert result['closed']==0 and st.positions['p']['qty']==.5
 result=cycle([],st,broker=Broker(),ledger=ledger,candles=lambda *a:[],now_ms=70000,cfg=Config(no_timeout=True),live_prices={'AAAUSDT':(99.8,69900,[(68000,99.8)])})
 assert result['closed']==1
 assert len([r for r in ledger.journal() if r['kind']=='partial'])==1

def test_exit_worker_never_calculates_signals_or_consumes_entry_inbox(tmp_path,monkeypatch):
 from tools.trade import screener_bot,screener_page
 def forbidden(*a):raise AssertionError('entry source used by exit worker')
 monkeypatch.setattr(screener_bot,'screener_rows',forbidden);monkeypatch.setattr(screener_bot,'load_trend',forbidden);monkeypatch.setattr(screener_page,'write',lambda *a,**kw:None)
 box=tmp_path/'inbox';box.mkdir();(box/'pending.json').write_text('{}')
 assert screener_bot.run_one(['--data',str(tmp_path),'--exits-only','--no-timeout'])==0
 assert (box/'pending.json').exists()

def test_entry_history_recovery_does_not_hold_profile_lock(tmp_path,monkeypatch):
 from tools.trade import screener_bot,screener_page
 st=initial();st.put(position());save_state(tmp_path/'state.json',st);calls=[]
 def recover(*args):
  with profile_lock(tmp_path):calls.append('unlocked')
  return []
 monkeypatch.setattr(screener_bot,'candles_1m',recover)
 monkeypatch.setattr(screener_bot,'screener_rows',lambda *a:([],[]))
 monkeypatch.setattr(screener_page,'write',lambda *a,**kw:None)
 assert screener_bot.run_one(['--data',str(tmp_path),'--trend','off','--no-timeout'])==0
 assert calls==['unlocked']

def test_entry_book_snapshot_never_requests_network_during_execution():
 from src.trade.entry_snapshot import EntryBroker,prepare
 from src.data.market import OrderBook,Level
 st=initial();calls=[];clock=[100.0]
 row=dict(symbol='AAAUSDT',tf='5m',kind='breakout',direction='long',entry=100,ts=1,triggered=True)
 book=OrderBook('binance','AAAUSDT',100000,[Level(99,10)],[Level(101,10)])
 broker=prepare([row],st,Config(),fetch=lambda *a:calls.append(a) or book,clock=lambda:clock[0])
 assert broker.mid('AAAUSDT')==100
 assert broker.execute('AAAUSDT','buy',1).price==101 and len(calls)==1
 clock[0]=106
 with pytest.raises(ValueError):broker.mid('AAAUSDT')
 assert broker.execute('AAAUSDT','buy',1) is None and len(calls)==1
