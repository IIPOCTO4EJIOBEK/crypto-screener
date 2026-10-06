"""Two-account paper experiment on fixed disputed Kronos origins; no API orders."""
import csv,json,argparse
from pathlib import Path

def leg(candles,side,notional=1000,stop_pct=.01,target_pct=.02,fee=.0005,slippage=.0001,funding_events=()):
 entry=float(candles[0]['open'])*(1+side*slippage);qty=notional/entry
 stop=entry*(1-side*stop_pct);target=entry*(1+side*target_pct);exit=float(candles[-1]['close']);reason='timeout';end=candles[-1]['timestamps']
 for c in candles:
  o,h,l=float(c['open']),float(c['high']),float(c['low'])
  if (l<=stop if side==1 else h>=stop):exit=min(o,stop) if side==1 else max(o,stop);reason='stop';end=c['timestamps'];break
  if (h>=target if side==1 else l<=target):exit=target;reason='target';end=c['timestamps'];break
 exit*=1-side*slippage
 # Funding charged only while this leg is open; timestamps interpreted below by caller.
 from datetime import datetime,timezone
 start_ms=int(datetime.fromisoformat(candles[0]['timestamps']).replace(tzinfo=timezone.utc).timestamp()*1000)
 end_ms=int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp()*1000)
 paid=side*qty*entry*sum(float(r['fundingRate']) for r in funding_events if start_ms<int(r['fundingTime'])<=end_ms)
 costs=fee*qty*(entry+exit);pnl=side*qty*(exit-entry)-costs-paid
 return dict(side=side,entry=entry,exit=exit,qty=qty,reason=reason,end=end,fee=costs,funding=paid,pnl=pnl)


def protected_pair(candles, mode="basket_trail", notional=1000, fee=.0005,
                   slippage=.0001, funding_events=(), activate=5, giveback=3):
 """Offline joint control at candle CLOSE; per-leg stop/target retains priority.

 Stops use adverse gap fills. A simultaneous stop/target uses the stop.
 Basket decisions use only the currently closed candle, never its future extrema.
 """
 from datetime import datetime,timezone
 def ms(t):return int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()*1000)
 start=ms(candles[0]['timestamps']); states=[]; peak=None; trigger=None
 for side in (1,-1):
  entry=float(candles[0]['open'])*(1+side*slippage)
  states.append(dict(side=side,entry=entry,qty=notional/entry,done=False,
                     stop=entry*(1-side*.01),target=entry*(1+side*.02)))
 def value(x,px,t,reason):
  exit=px*(1-x['side']*slippage)
  funding=x['side']*notional*sum(float(r['fundingRate']) for r in funding_events if start<int(r['fundingTime'])<=ms(t))
  costs=fee*x['qty']*(x['entry']+exit)
  return dict(side=x['side'],entry=x['entry'],exit=exit,qty=x['qty'],reason=reason,
              end=t,fee=costs,funding=funding,pnl=x['side']*x['qty']*(exit-x['entry'])-costs-funding)
 for c in candles:
  o,h,l,close=map(float,(c['open'],c['high'],c['low'],c['close']));stopped=False
  for x in states:
   if x['done']:continue
   up=x['side']==1
   if l<=x['stop'] if up else h>=x['stop']:
    px=min(o,x['stop']) if up else max(o,x['stop']);why='stop';stopped=True
   elif h>=x['target'] if up else l<=x['target']:
    px=x['target'];why='target'
   else:continue
   x['result']=value(x,px,c['timestamps'],why);x['done']=True
  net=sum(x['result']['pnl'] if x['done'] else value(x,close,c['timestamps'],'mark')['pnl'] for x in states)
  if mode=='close_after_stop' and stopped:trigger='pair_stop_close'
  if mode=='basket_trail':
   if net>=activate:peak=max(peak if peak is not None else net,net)
   if peak is not None and net<=peak-giveback:trigger='pair_profit_trail'
  if trigger:
   for x in states:
    if not x['done']:x['result']=value(x,close,c['timestamps'],trigger);x['done']=True
  if all(x['done'] for x in states):break
 for x in states:
  if not x['done']:x['result']=value(x,float(candles[-1]['close']),candles[-1]['timestamps'],'timeout')
 return dict(long=states[0]['result'],short=states[1]['result'],
             pair_pnl=sum(x['result']['pnl'] for x in states),trigger=trigger,peak_net=peak)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 pairs=[]
 for small in a.input.glob('*_small_scores.csv'):
  prefix=small.name.replace('_small_scores.csv','');symbol,tf=prefix.split('_')
  base=list(csv.DictReader((a.input/f'{prefix}_base_scores.csv').open()))
  sm=list(csv.DictReader(small.open()));bars=list(csv.DictReader((a.input/f'{prefix}_input.csv').open()))
  funding=json.loads((a.input/f'{symbol}_funding.json').read_text())
  for x,y in zip(sm,base):
   assert x['origin']==y['origin']
   if x['split']!='test' or int(x['side'])*int(y['side'])!=-1:continue
   start=next(i for i,b in enumerate(bars) if b['timestamps']==x['entry_time']);window=bars[start:start+8]
   long=leg(window,1,funding_events=funding);short=leg(window,-1,funding_events=funding)
   pairs.append(dict(symbol=symbol,tf=tf,origin=x['origin'],small_side=int(x['side']),base_side=int(y['side']),long=long,short=short,pair_pnl=long['pnl']+short['pnl'],pair_return_pct=(long['pnl']+short['pnl'])/2000*100,flat_pnl=0,protected={mode:protected_pair(window,mode,funding_events=funding) for mode in ['close_after_stop','basket_trail']}))
 report=dict(experimental=True,orders_enabled=False,disputed_rule='Both models take nonzero opposite directions on the same fixed test origin.',capital_each=1000,notional_each=1000,stop_pct=.01,target_pct=.02,horizon_candles=8,n=len(pairs),pair_pnl_sum=sum(r['pair_pnl'] for r in pairs),long_pnl_sum=sum(r['long']['pnl'] for r in pairs),short_pnl_sum=sum(r['short']['pnl'] for r in pairs),fees_sum=sum(r[k]['fee'] for r in pairs for k in ['long','short']),both_stop=sum(r['long']['reason']=='stop' and r['short']['reason']=='stop' for r in pairs),limitations=['Independent hypothetical trades; results summed, not a sequential portfolio equity curve.','No parameter tuning; stop checked before target when OHLC is ambiguous.','Funding cut at the exit candle opening; intrabar ordering unknown.','Fixed slippage; historical executable depth is unavailable.','Small recent sample; not suitable for real trading approval.'],pairs=pairs)
 report['protection_comparison']={mode:dict(pair_pnl_sum=sum(p['protected'][mode]['pair_pnl'] for p in pairs),
                      fees_sum=sum(p['protected'][mode][k]['fee'] for p in pairs for k in ['long','short']),
                      triggers=sum(p['protected'][mode]['trigger'] is not None for p in pairs),
                      worst_pair=min((p['protected'][mode]['pair_pnl'] for p in pairs),default=0))
                      for mode in ['close_after_stop','basket_trail']}
 report['protection_parameters']={'activation_net_usdt':5,'giveback_net_usdt':3,'decision':'candle close only; gap and within-candle losses may exceed the intended floor'}
 a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2),'utf-8');print(json.dumps({k:v for k,v in report.items() if k!='pairs'},indent=2))
if __name__=='__main__':main()
