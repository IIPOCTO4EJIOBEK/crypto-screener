"""Publish position invalidations without a profile writer lock or network."""
import json,time,os
from pathlib import Path
from src.trade.atomic_store import read_state
from src.trade.intraday import Position
from src.trade.thesis import invalidation

def round_once(root,market,now=None):
    start=time.monotonic();now=int(time.time()*1000) if now is None else now;cache={};rows=[];missing=set();count=0
    for folder in sorted(Path(root).glob('screener-*')):
        if not folder.is_dir():continue
        state=read_state(folder) or {}
        for raw in state.get('positions',{}).values():
            p=Position(**raw)
            if p.qty*p.entry<=1e-6:continue
            count+=1;key=(p.symbol,p.tf)
            if key not in cache:
                try:cache[key]=json.loads((Path(market)/(p.symbol+'_'+p.tf+'.json')).read_text('utf-8'))
                except (OSError,ValueError):cache[key]=[];missing.add('|'.join(key))
            row=invalidation(p,cache[key],now)
            if row:rows.append(row)
    return dict(updated_ms=now,positions=count,signals=rows,missing=sorted(missing),duration_seconds=round(time.monotonic()-start,3))

def run():
    root=Path('/opt/crypto-trade/data/trade');market=Path('/opt/crypto-screener/docs/live/kl')
    while True:
        result=round_once(root,market);dest=root/'thesis-status.json';tmp=dest.with_suffix('.tmp')
        tmp.write_text(json.dumps(result,ensure_ascii=False),'utf-8');os.replace(tmp,dest)
        print('thesis',result['positions'],'positions',len(result['signals']),'invalidations',result['duration_seconds'],'seconds',flush=True)
        time.sleep(10)

if __name__=='__main__':run()
