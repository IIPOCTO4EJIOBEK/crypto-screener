"""Confirmed structural reversal on the position timeframe or a higher one."""
import json
from pathlib import Path
from src.data.market import INTERVALS

def confirmed(position,row,price,now):
    tf=row.get('tf');duration=INTERVALS.get(tf,0)*1000
    if row.get('symbol')!=position.symbol or row.get('direction') not in ('long','short') or row['direction']==position.side:return False
    if not duration or duration<INTERVALS[position.tf]*1000:return False
    if row.get('kind') not in ('breakout','retest','structure_break') or not row.get('triggered'):return False
    ts=row.get('ts');ready=(row.get('ready_ms') or ts+duration) if isinstance(ts,(int,float)) else 0
    if not position.opened_ms<ready<=now or now-ready>=duration:return False
    level=row.get('trigger_level');closed=row.get('_latest_closed')
    if not level or closed is None:return False
    sign=1 if row['direction']=='long' else -1
    return sign*(price-level)>0 and sign*(closed-level)>0

def load(path,market,now):
    try:
        data=json.loads(Path(path).read_text('utf-8'))
        if not 0<=now-data['built_unix']*1000<=900000:return []
    except (OSError,ValueError,KeyError,TypeError):return []
    rows=[]
    for raw in data.get('signals',[]):
        row=dict(raw);duration=INTERVALS.get(row.get('tf'),0)*1000
        if not duration or row.get('kind') not in ('breakout','retest','structure_break'):continue
        try:
            bars=json.loads((Path(market)/(row['symbol']+'_'+row['tf']+'.json')).read_text('utf-8'))
            closed=[c for c in bars if c[0]+duration<=now];last=closed[-1]
            if last[0]+duration!=now//duration*duration:continue
            row['_latest_closed']=float(last[4]);rows.append(row)
        except (OSError,ValueError,KeyError,IndexError,TypeError):continue
    return rows
