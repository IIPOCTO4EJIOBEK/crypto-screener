import json
from src.trade import trend_display
from tests.test_fast_monitor import position

def test_fluid_old_long_and_current_short_are_distinct(tmp_path,monkeypatch):
 p=position();p.symbol='FLUIDUSDT';p.tf='1h';p.trend='long'
 data=dict(built_unix=100,coins={'FLUIDUSDT':{'15m':'long','1h':'short','overall':'flat'}})
 path=tmp_path/'trend.json';path.write_text(json.dumps(data));monkeypatch.setenv('SCREENER_TREND_SRC',str(path))
 f=trend_display.fields(p,trend_display.load(101000))
 assert f['trend_at_entry']=='long' and f['trend_now']=='short' and f['trend_conflict']
 assert p.side=='long' and p.trend=='long'
 assert 'против позиции' in trend_display.text(p,data)
 assert not trend_display.fields(p,trend_display.load(1000001))['trend_conflict']
 assert 'нет свежих данных' in trend_display.text(p,{})
