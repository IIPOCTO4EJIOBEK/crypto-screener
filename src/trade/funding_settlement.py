"""Deferred paper accounting: fetch outside profile lock, settle once in SQLite."""
import json
import math
import time
from src.data.market import binance_funding_history
from src.trade.atomic_store import database,transaction
from src.trade.intraday import load_state,msk_day
from src.trade.ledger import Ledger

def amount(row,fetch=binance_funding_history):
    start=int(row['opened_ms']);end=int(row['exit_ts']);records={};cursor=start+1
    for _ in range(100):
        page=fetch(row['symbol'],cursor,end)
        for stamp,rate in page:
            if not math.isfinite(rate):raise ValueError('invalid funding rate')
            if start<stamp<=end:records[stamp]=rate
        if len(page)<1000:break
        following=max(t for t,_ in page)+1
        if following<=cursor:raise ValueError('funding pagination stalled')
        cursor=following
    else:raise ValueError('funding pagination limit')
    weighted=sum(float(leg['qty'])*sum(rate for stamp,rate in records.items() if stamp<=leg['end_ms']) for leg in row['funding_legs'])
    value=(1 if row['side']=='long' else -1)*float(row['entry'])*weighted
    if not math.isfinite(value):raise ValueError('invalid funding total')
    return value

def settle(root,ident,value,now_ms=None):
    now_ms=now_ms if now_ms is not None else int(time.time()*1000)
    ledger=Ledger(root)
    with transaction(ledger) as tx:
        saved=tx.connection.execute('SELECT payload FROM journal WHERE id=?',(ident,)).fetchone()
        if not saved:return False
        row=json.loads(saved[0])
        if not row.get('funding_pending'):return False
        delta=value-float(row.get('funding') or 0)
        state=load_state(ledger.state_path,1000,now_ms)
        state.cash-=delta
        if state.day==msk_day(row['exit_ts']):state.day_pnl-=delta
        state.pending_funding=max(0,state.pending_funding-1)
        row.update(funding=value,funding_pending=False,funding_settled_ms=now_ms)
        net=row['pnl']-row['fee']-value
        if row.get('funding_risk_quote'):row['r_net']=net/row['funding_risk_quote']
        tx.updates.append((ident,row))
        ledger.log('funding_settled',key=row['key'],close_event_id=row.get('event_id'),symbol=row['symbol'],funding=value,cash_delta=-delta,net=net)
        eq=state.equity();state.peak=max(state.peak,eq)
        limit=row.get('funding_dd_limit',.25)
        if state.peak>0 and eq<state.peak*(1-limit) and not ledger.halted:
            ledger.halt('просадка после уточнения funding');ledger.log('halt',equity=eq,peak=state.peak)
        # Funding can change the sign of the latest result; rebuild the trailing streak.
        journal=ledger.journal();cutoff=max((r['ts'] for r in journal if r['kind']=='pause'),default=0)
        closed=[r for r in journal if r['kind']=='close' and r['ts']>cutoff]
        closed=[row if r.get('event_id')==row.get('event_id') else r for r in closed]
        state.streak=0
        for item in reversed(closed):
            if item['pnl']-item['fee']-(item.get('funding') or 0)>=0:break
            state.streak+=1
        rules=row.get('rules') or {};pause_after=rules.get('pause_after',0)
        if pause_after and state.streak>=pause_after:
            state.cooldown['__all__']=now_ms+rules.get('pause_min',60)*60_000;state.streak=0
            ledger.log('pause',until=state.cooldown['__all__'],reason='серия убытков после уточнения funding')
        pct=100*net/(row['entry']*row['qty']) if row.get('qty') else 0
        message=f"Funding уточнён: {row['symbol']} {row['tf']}, {value:+.6f} USDT; итог сделки {net:+.4f} USDT ({pct:+.2f}%), {row['r_net']:+.2f} R · бумажная торговля"
        tx.commit(state,[message],notify=bool(delta))
        return True

def pending(root):
    import sqlite3
    from contextlib import closing
    if not database(root).exists():return []
    with closing(sqlite3.connect(database(root),timeout=5)) as conn:
        return [(ident,row) for ident,payload in conn.execute('SELECT id,payload FROM journal') if (row:=json.loads(payload)).get('funding_pending')]

def process(root,fetch=binance_funding_history,now_ms=None):
    now_ms=now_ms if now_ms is not None else int(time.time()*1000);done=0
    for ident,row in pending(root):
        # Let freshly published funding history arrive; this wait never blocks exits.
        if now_ms-row['exit_ts']<60_000:continue
        value=amount(row,fetch)  # no profile writer lock during HTTP or limiter wait
        done+=settle(root,ident,value,now_ms)
    return done
