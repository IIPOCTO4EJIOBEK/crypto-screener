"""Read-only audit of every paper profile; raw output is kept outside Git."""
import json, time, statistics
from collections import defaultdict, Counter
from pathlib import Path
from src.trade.atomic_store import read_state
from src.trade.ledger import Ledger
from src.data.market import INTERVALS

def audit(root, market, trend_path):
    now=int(time.time()*1000);root=Path(root);market=Path(market)
    try:trend=json.loads(Path(trend_path).read_text()).get('coins',{})
    except (OSError,ValueError):trend={}
    cached={}
    def bars(symbol,tf):
        key=(symbol,tf)
        if key not in cached:
            try:cached[key]=json.loads((market/(symbol+'_'+tf+'.json')).read_text())
            except (OSError,ValueError):cached[key]=[]
        return cached[key]
    result={'updated_ms':now,'profiles':{},'positions':[],'groups':[],'candle_gaps':[]}
    grouped=defaultdict(list)
    for path in sorted(root.glob('screener-*')):
        if not path.is_dir():continue
        st=read_state(path) or {};journal=Ledger(path).journal()
        closes=[e for e in journal if e.get('kind')=='close' and abs(e.get('qty',0)*e.get('entry',0))>1e-6]
        for event in closes:
            row=dict(event);row['profile']=path.name
            grouped[(path.name,row.get('formation'),row.get('tf'),row.get('side'))].append(row)
        pos=list(st.get('positions',{}).values())
        result['profiles'][path.name]={'positions':len(pos),'closed':len(closes),'reasons':dict(Counter(e.get('reason') for e in closes)),
            'net':sum(e.get('pnl',0)-e.get('fee',0)-e.get('funding',0) for e in closes),
            'last_errors':[e for e in journal if e.get('kind')=='error'][-3:]}
        for p in pos:
            sign=1 if p['side']=='long' else -1;entry=p['entry'];risk=p.get('risk0') or abs(entry-p['stop']);tf=p['tf'];duration=INTERVALS[tf]*1000
            current=trend.get(p['symbol'],{});trend_tf=current.get('15m' if tf=='5m' else tf)
            closed_bars=[b for b in bars(p['symbol'],tf) if b[0]+duration<=now]
            before=[b for b in closed_bars if b[0]+duration<=p['opened_ms']][-14:]
            ranges=[max(b[2]-b[3],abs(b[2]-before[i-1][4]),abs(b[3]-before[i-1][4])) if i else b[2]-b[3] for i,b in enumerate(before)]
            atr=statistics.mean(ranges) if ranges else None
            complete=[b for b in closed_bars if b[0]>=p['opened_ms']]
            mfe=max([sign*((b[2] if sign>0 else b[3])-entry)/risk for b in complete],default=None)
            mae=min([sign*((b[3] if sign>0 else b[2])-entry)/risk for b in complete],default=None)
            result['positions'].append(dict(profile=path.name,key=p['key'],symbol=p['symbol'],tf=tf,kind=p['kind'],side=p['side'],entry=entry,stop=p['stop'],target=p['target'],
                opened_ms=p['opened_ms'],mark=p.get('mark'),qty=p['qty'],gross=sign*((p.get('mark') or entry)-entry)*p['qty'],
                stop_pct=abs(entry-p['stop'])/entry*100,initial_stop_pct=risk/entry*100,stop_atr=risk/atr if atr else None,
                rr=sign*(p['target']-entry)/risk if p['target'] else None,trend=trend_tf,overall=current.get('overall'),entry_trend=p.get('trend'),measured_r=p.get('measured_r'),measured_n=p.get('measured_n'),
                mfe_r=mfe,mae_r=mae,history_complete=bool(closed_bars and closed_bars[0][0]<=p['opened_ms'] and closed_bars[-1][0]+duration==now//duration*duration and all(b[0]-a[0]==duration for a,b in zip(complete,complete[1:]))),
                reasons=p.get('reasons',[]),dust=p['qty']*entry<=1e-6))
    for (profile,kind,tf,side),events in grouped.items():
        net=[e.get('pnl',0)-e.get('fee',0)-e.get('funding',0) for e in events]
        loss=-sum(v for v in net if v<0)
        result['groups'].append(dict(profile=profile,kind=kind,tf=tf,side=side,n=len(events),stops=sum(e.get('reason')=='stop' for e in events),net=sum(net),
            wins=sum(v>0 for v in net),pf=sum(v for v in net if v>0)/loss if loss else None,
            fees=sum(e.get('fee',0) for e in events),funding_pending=sum(bool(e.get('funding_pending')) for e in events)))
    needed={}
    for p in result['positions']:
        st=read_state(root/p['profile']);raw=st['positions'][p['key']];since=max(raw['opened_ms'],raw.get('last_check_ms',0))
        needed[p['symbol']]=min(needed.get(p['symbol'],since),since)
    for symbol,since in needed.items():
        if since//60000==now//60000:continue # no new CLOSED minute required
        ws=bars(symbol,'1m')
        try:repair=json.loads((root.parent/'minute-recovery'/(symbol+'.json')).read_text())
        except (OSError,ValueError):repair=[]
        merged={int(r[0]):r for r in repair};merged.update({int(r[0]):r for r in ws});required=sorted(t for t in merged if t>=since//60000*60000)
        gaps=[(a,b) for a,b in zip(required,required[1:]) if b-a!=60000]
        if not required or gaps or required[0]>since//60000*60000 or required[-1]<(now//60000-1)*60000:
            result['candle_gaps'].append(dict(symbol=symbol,since=since,now=now,start=required[0] if required else None,end=required[-1] if required else None,gaps=gaps[:3]))
    return result

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--root',default='/opt/crypto-trade/data/trade');ap.add_argument('--market',default='/opt/crypto-screener/docs/live/kl');a=ap.parse_args()
    print(json.dumps(audit(a.root,a.market,Path(a.market).parent/'trend-now.json'),ensure_ascii=False))
