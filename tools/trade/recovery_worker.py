"""Read-only position scan and REST repairs, never hold a profile writer lock."""
import json,os,time
from pathlib import Path
from dataclasses import asdict
from src.trade.atomic_store import read_state
from src.trade.monitor_market import MinuteCache,RecoveryPending
from tools.trade.screener_bot import ROOT,candles_1m
from tools.trade.run import load_env

def round_once(trade_root,market_root,recovery_root,fetch=candles_1m):
    needed={};recovery_root.mkdir(parents=True,exist_ok=True)
    for root in trade_root.glob('screener-*'):
        if not root.is_dir():continue
        state=read_state(root) or {}
        for p in state.get('positions',{}).values():
            since=max(p['opened_ms'],p.get('last_check_ms',0));s=p['symbol'];needed[s]=min(needed.get(s,since),since)
    cache=MinuteCache(market_root,None,recovery_root=recovery_root,local_only=True);repaired=[];errors=[]
    # Keep live subscriptions for positions that left the turnover universe.
    path=trade_root/'position-symbols.json';tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(symbols=sorted(needed),updated_ms=int(time.time()*1000))))
    os.replace(tmp,path)
    for s,since in needed.items():
        try:cache.candles(s,since)
        except RecoveryPending:
            try:
                cs=fetch(s,since//60000*60000)
                if not cs:raise ValueError('empty recovery')
                data=[[c.ts,c.open,c.high,c.low,c.close,c.quote_volume] for c in cs]
                path=recovery_root/(s+'.json');tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data));os.replace(tmp,path)
                # Reject incomplete REST recovery too; leave cursor untouched.
                cache.candles(s,since);repaired.append(s)
            except Exception as exc:errors.append(dict(symbol=s,error=type(exc).__name__))
    return dict(repaired=repaired,errors=errors,symbols=len(needed))

def run():
    load_env();trade=ROOT/'data/trade';market=Path('/opt/crypto-screener/docs/live/kl');repair=ROOT/'data/minute-recovery'
    while True:
        start=time.monotonic()
        try:status=round_once(trade,market,repair)
        except Exception as exc:status=dict(errors=[dict(error=type(exc).__name__)])
        status.update(updated_ms=int(time.time()*1000),duration_seconds=round(time.monotonic()-start,3))
        path=trade/'recovery-status.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(status));os.replace(tmp,path);print(json.dumps(status),flush=True);time.sleep(5)

if __name__=='__main__':run()
