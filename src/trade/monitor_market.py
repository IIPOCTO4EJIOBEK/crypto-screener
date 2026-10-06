"""Share minute history between profiles; prefer local WebSocket projections."""
import json,math,time
from pathlib import Path
from src.data.market import Candle

class RecoveryPending(ValueError):pass

class MinuteCache:
 def __init__(self,root,fallback,recovery_root=None,local_only=False):self.root=Path(root);self.fallback=fallback;self.cache={};self.sources={};self.recovery_root=Path(recovery_root) if recovery_root else None;self.local_only=local_only
 def candles(self,symbol,since):
  saved=self.cache.get(symbol)
  if saved and saved[0]<=since:return saved[1]
  now=int(time.time()*1000)
  try:
   try:data=json.loads((self.root/(symbol+'_1m.json')).read_text('utf-8'))
   except (OSError,ValueError):data=[]
   source='websocket'
   if self.recovery_root:
    try:
     repair=json.loads((self.recovery_root/(symbol+'.json')).read_text('utf-8'))
     merged={int(r[0]):r for r in repair};merged.update({int(r[0]):r for r in data});data=sorted(merged.values(),key=lambda r:r[0]);source='websocket+recovery'
    except (OSError,ValueError,TypeError,IndexError,KeyError):pass
   if not isinstance(data,list) or not data:raise ValueError('empty local minute history')
   # Only the interval at/after the position cursor can affect its exits.
   # Old WS bars may be separated from a complete recent REST repair.
   start=since//60_000*60_000
   cs=[Candle(int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),0,float(r[5]),0) for r in data if int(r[0])>=start]
   if not cs:raise ValueError('empty required minute history')
   if cs[0].ts>since//60_000*60_000 or cs[-1].ts<(now//60_000-1)*60_000:raise ValueError('local recovery range incomplete')
   if any(b.ts-a.ts!=60_000 for a,b in zip(cs,cs[1:])):raise ValueError('gap in local minute history')
   if any(not all(math.isfinite(x) and x>0 for x in (c.open,c.high,c.low,c.close)) for c in cs):raise ValueError('invalid local price')
   self.cache[symbol]=(cs[0].ts,cs);self.sources[symbol]=source;return cs
  except (OSError,ValueError,TypeError,IndexError,KeyError):
   if self.local_only:
    self.sources[symbol]='waiting-recovery';raise RecoveryPending('minute history incomplete; repair worker pending')
   cs=self.fallback(symbol,since);self.cache[symbol]=(since,cs);self.sources[symbol]='rest-recovery';return cs
 def live(self,symbols,now):
  out={}
  for symbol in symbols:
   try:
    x=json.loads((self.root/(symbol+'.live.json')).read_text('utf-8'));stamp=int(x['t']);price=float(x['k']['1m'][4])
    if math.isfinite(price) and price>0 and 0<=now-stamp<=10_000:
     trace=[];last=0
     for item in x.get('ticks',[]):
      t,p=int(item[0]),float(item[1])
      if last<t<=stamp and math.isfinite(p) and p>0:trace.append((t,p));last=t
     out[symbol]=(price,stamp,trace)
   except (OSError,ValueError,KeyError,TypeError,IndexError):pass
  return out
