"""Entry trend is historical; show current TF direction separately and freshness."""
import json,os
from pathlib import Path
from src.trade.intraday import TREND_TF

def load(now_ms):
    try:
        d=json.loads(Path(os.environ.get('SCREENER_TREND_SRC','/opt/crypto-screener/docs/live/trend-now.json')).read_text('utf-8'))
        if not 0<=now_ms-d['built_unix']*1000<=900000:return {}
        return d
    except (OSError,ValueError,KeyError,TypeError):return {}

def fields(p,data):
    now=data.get('coins',{}).get(p.symbol,{}).get(TREND_TF.get(p.tf,p.tf),'')
    return dict(trend_at_entry=p.trend,trend_now=now,trend_conflict=now in ('long','short') and now!=p.side,trend_updated_ms=int(data.get('built_unix',0)*1000) or None)

def text(p,data):
    f=fields(p,data)
    return 'на входе: '+(p.trend or 'не записан')+'; сейчас: '+(f['trend_now'] or 'нет свежих данных')+(' ⚠ против позиции' if f['trend_conflict'] else '')
