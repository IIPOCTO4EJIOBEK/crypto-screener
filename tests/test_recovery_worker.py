import json,time
import pytest
from pathlib import Path
from src.trade.monitor_market import MinuteCache,RecoveryPending
from src.trade.atomic_store import transaction,profile_lock
from src.trade.ledger import Ledger
from src.trade.intraday import BotState,Position
from src.data.market import Candle
from tools.trade.recovery_worker import round_once

def bars():
 now=int(time.time()*1000)//60000*60000
 return [[now-120000,100,102,99,101,10],[now-60000,101,102,100,101,10],[now,101,102,100,101,10]]

def test_local_only_never_calls_rest_on_missing_or_corrupt_history(tmp_path):
 def forbidden(*a):raise AssertionError('network in monitor')
 cache=MinuteCache(tmp_path,forbidden,recovery_root=tmp_path/'repair',local_only=True)
 with pytest.raises(RecoveryPending):cache.candles('AAAUSDT',0)
 (tmp_path/'AAAUSDT_1m.json').write_text('{}')
 with pytest.raises(RecoveryPending):cache.candles('AAAUSDT',0)

def test_repair_merges_with_newer_ws_and_preserves_gap_detection(tmp_path):
 data=bars();market=tmp_path/'kl';repair=tmp_path/'repair';market.mkdir();repair.mkdir()
 (repair/'AAAUSDT.json').write_text(json.dumps(data[:2]));data[-1][4]=102
 (market/'AAAUSDT_1m.json').write_text(json.dumps(data[1:]))
 cache=MinuteCache(market,None,recovery_root=repair,local_only=True)
 assert cache.candles('AAAUSDT',data[0][0])[-1].close==102
 (repair/'AAAUSDT.json').write_text('[]');(market/'AAAUSDT_1m.json').write_text(json.dumps([data[0],data[2]]))
 with pytest.raises(RecoveryPending):MinuteCache(market,None,recovery_root=repair,local_only=True).candles('AAAUSDT',data[0][0])

def test_recovery_fetch_runs_outside_position_lock(tmp_path):
 trade=tmp_path/'trade';root=trade/'screener-test';ledger=Ledger(root);data=bars();start=data[0][0]+1000
 state=BotState(cash=1000,peak=1000,start_equity=1000)
 state.put(Position(key='p',symbol='AAAUSDT',kind='x',title='x',tf='5m',side='long',qty=1,entry=100,signal_entry=100,stop=98,target=104,opened_ms=start,expires_ms=2**62,fee_in=.05))
 with transaction(ledger) as tx:tx.commit(state)
 def fetch(symbol,since):
  with profile_lock(root):pass
  assert since==data[0][0]
  return [Candle(r[0],*r[1:5],0,r[5],0) for r in data]
 result=round_once(trade,tmp_path/'kl',tmp_path/'repair',fetch)
 assert result['repaired']==['AAAUSDT'] and not result['errors']
 assert round_once(trade,tmp_path/'kl',tmp_path/'repair',lambda *a:(_ for _ in ()).throw(AssertionError('repeat REST')))['repaired']==[]

def test_exit_main_never_polls_telegram(tmp_path,monkeypatch):
 from tools.trade import screener_bot,tg_control
 p=tmp_path/'profiles.json';p.write_text('[[]]');monkeypatch.setattr(screener_bot,'PROFILES',p);monkeypatch.setenv('TELEGRAM_CONTROL_ENABLED','1')
 monkeypatch.setattr(tg_control,'run',lambda *a:(_ for _ in ()).throw(AssertionError('TG in monitor')))
 monkeypatch.setattr(screener_bot,'run_one',lambda *a:0)
 assert screener_bot.main(['--exits-only'])==0

def test_real_exit_profile_retains_cursor_without_network_on_gap(tmp_path,monkeypatch):
 from tools.trade import screener_bot,screener_page
 from tests.test_fast_monitor import initial,position
 from src.trade.intraday import save_state,load_state
 state=initial();state.put(position());save_state(tmp_path/'state.json',state);calls=[]
 monkeypatch.setenv('SCREENER_RECOVERY_DIR',str(tmp_path/'repair'))
 monkeypatch.setattr(screener_bot,'candles_1m',lambda *a:calls.append(a) or [])
 monkeypatch.setattr(screener_page,'write',lambda *a,**k:None)
 assert screener_bot.run_one(['--data',str(tmp_path),'--exits-only','--no-timeout'])==0
 assert not calls and load_state(tmp_path/'state.json',0,0).pos()[0].last_check_ms==state.pos()[0].last_check_ms
