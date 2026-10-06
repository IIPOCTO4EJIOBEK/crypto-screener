import json
from src.trade.reversal import confirmed,load
from tests.test_fast_monitor import position

def signal(**extra):
    r=dict(symbol='AAAUSDT',tf='5m',direction='short',kind='structure_break',triggered=True,ts=0,ready_ms=20000,trigger_level=100,_latest_closed=99);r.update(extra);return r

def test_reversal_must_be_new_confirmed_and_not_lower_timeframe():
    p=position();assert confirmed(p,signal(),99,21000)
    assert not confirmed(p,signal(ready_ms=9000),99,21000)
    assert not confirmed(p,signal(ready_ms=22000),99,21000)
    assert not confirmed(p,signal(),101,21000)
    assert not confirmed(p,signal(_latest_closed=101),99,21000)
    assert not confirmed(p,signal(trigger_level=None),99,21000)
    p.tf='1h';assert not confirmed(p,signal(),99,21000)

def test_reversal_loader_requires_fresh_closed_candle(tmp_path):
    source=tmp_path/'setups.json';market=tmp_path/'kl';market.mkdir()
    source.write_text(json.dumps(dict(built_unix=600,signals=[signal(ts=300000,ready_ms=None)])))
    (market/'AAAUSDT_5m.json').write_text('[[300000,100,101,98,99,1]]')
    assert load(source,market,610000)[0]['_latest_closed']==99
    assert load(source,market,910000)==[]

def test_fast_exit_profile_reads_reversal_file_without_signal_calculation(tmp_path,monkeypatch):
    from tools.trade import screener_bot,screener_page
    from tests.test_fast_monitor import initial
    from src.trade.intraday import save_state
    from src.trade.reversal import load as actual_load
    state=initial();state.put(position());save_state(tmp_path/'state.json',state);seen=[]
    monkeypatch.setattr('src.trade.reversal.load',lambda *a:seen.append(a) or [])
    monkeypatch.setattr(screener_bot,'screener_rows',lambda *a:(_ for _ in ()).throw(AssertionError('signal calculation in exit monitor')))
    monkeypatch.setattr(screener_page,'write',lambda *a,**k:None)
    monkeypatch.setenv('SCREENER_RECOVERY_DIR',str(tmp_path/'repair'))
    monkeypatch.setenv('SCREENER_BOOK_DIR',str(tmp_path/'books'))
    assert screener_bot.run_one(['--data',str(tmp_path),'--exits-only','--no-timeout','--exit-on-opposite'])==0
    assert len(seen)==1

def test_stop_keeps_priority_over_confirmed_reversal(tmp_path):
    from tests.test_screener_bot import setup,run,row,c,T0,MIN
    from src.trade.intraday import Config
    book,broker,ledger,state,data=setup(tmp_path);run(state,broker,ledger,data,[row()],T0)
    opposite=row(direction='short',kind='breakout',entry=100,stop=102,target=94,ts=T0-4*MIN,trigger_level=101,_latest_closed=100)
    data['c']=[c(T0,100,100,97,99)]
    result=run(state,broker,ledger,data,[opposite],T0+MIN,Config(exit_on_opposite=True))
    assert result['closed']==1
    assert next(r for r in ledger.journal() if r['kind']=='close')['reason']=='stop'
