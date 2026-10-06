"""Separate funding reconciliation, safe to retry after restart."""
import argparse,json,os,time
from pathlib import Path
from src.trade.funding_settlement import process,pending
from tools.trade.screener_bot import ROOT
from tools.trade.run import load_env

def run(once=False):
    load_env();root=ROOT/'data/trade'
    while True:
        started=time.monotonic();done=0;errors=[];waiting=0
        for profile in sorted(root.glob('screener-*')):
            if not profile.is_dir():continue
            try:done+=process(profile)
            except Exception as exc:errors.append(dict(profile=profile.name,error=type(exc).__name__))
            waiting+=len(pending(profile))
        status=dict(updated_ms=int(time.time()*1000),duration_seconds=round(time.monotonic()-started,3),settled=done,pending=waiting,errors=errors)
        path=root/'funding-status.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(status));os.replace(tmp,path)
        print(json.dumps(status),flush=True)
        if once:return
        time.sleep(20)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');run(p.parse_args().once)
