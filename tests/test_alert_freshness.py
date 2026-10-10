import json,queue
from pathlib import Path
from tools.live.alerts import Alerts


def bare(tmp_path):
    a=Alerts.__new__(Alerts)
    a.state_path=tmp_path/'alerts_sent.json'
    a.signals=[];a.tracks=[];a.token='test';a.q=queue.Queue()
    a.link=lambda mid:'https://example.test/signal'
    return a


def test_signal_age_starts_at_candle_close(tmp_path,monkeypatch):
    a=bare(tmp_path)
    # API3 10:40 candle closed 10:45, alert arrived 10:49:42.
    opened=1791272400000
    a.signals=[dict(symbol='API3USDT',tf='5m',ts=opened,added=opened+582000)]
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:(opened+655000)/1000)
    a._write_signals()
    data=json.loads((tmp_path/'alert_signals.json').read_text())
    assert data['signals'][0]['age_candles']==1
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:(opened+900000)/1000)
    a._write_signals()
    assert json.loads((tmp_path/'alert_signals.json').read_text())['signals']==[]


def test_do_not_count_prepublication_extrema(tmp_path,monkeypatch):
    a=bare(tmp_path)
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:150)
    a.tracks=[dict(mid=1,sym='API3USDT',tf='5m',dir='long',entry=100,stop=98,target=110,rr=5,t=120000,hit=[],done=False)]
    a.candles=lambda *args:[[0,100,109,97,100]]
    a.follow()
    assert a.q.empty() and not a.tracks[0]['done']
    a.candles=lambda *args:[[0,100,109,97,104.1]]
    a.follow()
    assert a.tracks[0]['hit']==[1,2]
    _,fn=a.q.get_nowait()
    assert 'исполнение ботом не подтверждено' in fn()[0]


def test_levels_already_reached_at_publication_are_not_new_hits(tmp_path):
    a=bare(tmp_path)
    a.candles=lambda *args:[[0,100,105,99,104.1]]
    a._track(1,dict(sym='API3USDT',direction='long',entry=100,stop=98,target=110,rr=5))
    assert a.tracks[0]['hit']==[1,2]
    assert a.tracks[0]['published_price']==104.1
    assert not a.tracks[0]['done']


def test_event_alert_keeps_its_original_ready_time(tmp_path,monkeypatch):
    a=bare(tmp_path)
    a.signals=[dict(symbol='API3USDT',tf='5m',ts=300000,ready_ms=300000,added=310000)]
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:900)
    a._write_signals()
    assert json.loads((tmp_path/'alert_signals.json').read_text())['signals']==[]


def test_same_setup_from_two_formations_is_sent_once(tmp_path,monkeypatch):
    # RLC 5m: «Пробой уровня» и «Ретест уровня» с одной сделкой ушли двумя сообщениями подряд.
    a=bare(tmp_path)
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:1000)
    assert not a._setup_repeat('RLCUSDT','5m','short',0.9503,0.9568)
    assert a._setup_repeat('RLCUSDT','5m','short',0.9503,0.9568)
    # TRX: повтор той же зоны на следующей свече, вход сдвинулся меньше 1R
    assert not a._setup_repeat('TRXUSDT','5m','short',0.33061,0.33262)
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:1000+354)
    assert a._setup_repeat('TRXUSDT','5m','short',0.33054,0.33262)
    # другая сторона или ТФ — отдельный сетап
    assert not a._setup_repeat('TRXUSDT','5m','long',0.33054,0.32900)
    assert not a._setup_repeat('TRXUSDT','15m','short',0.33054,0.33262)
    # через полчаса та же зона снова может прийти
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:1000+1801)
    assert not a._setup_repeat('RLCUSDT','5m','short',0.9503,0.9568)


def test_far_entry_is_a_new_setup(tmp_path,monkeypatch):
    a=bare(tmp_path)
    monkeypatch.setattr('tools.live.alerts.time.time',lambda:1000)
    assert not a._setup_repeat('RLCUSDT','5m','short',0.9503,0.9568)
    assert not a._setup_repeat('RLCUSDT','5m','short',0.9381,0.9449)


def test_second_alert_with_same_trade_does_not_double_follow_ups(tmp_path):
    a=bare(tmp_path)
    a.candles=lambda *args:[[0,0.95,0.951,0.949,0.9503]]
    t=dict(sym='RLCUSDT',direction='short',entry=0.9503,stop=0.9568,target=0.925,rr=3.9)
    a._track(4719,t)
    a._track(4720,t)
    assert [x['mid'] for x in a.tracks]==[4719]
    a.tracks[0]['done']=True
    a._track(4800,t)
    assert [x['mid'] for x in a.tracks]==[4719,4800]
