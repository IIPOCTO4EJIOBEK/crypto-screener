"""Independent paper exits/controls worker; never calculates or opens signals."""
import argparse,json,time,os
from pathlib import Path
from tools.trade.screener_bot import main,ROOT,LAST_MONITOR_STATS
from tools.trade.run import load_env
from src.trade.notification_outbox import drain_one

def run(argv=None):
 ap=argparse.ArgumentParser();ap.add_argument('--interval',type=float,default=5);ap.add_argument('--once',action='store_true');ap.add_argument('--notify-only',action='store_true');a=ap.parse_args(argv)
 if a.interval<1:ap.error('interval must be >=1')
 load_env();rotation=0
 while True:
  started=time.monotonic();ok=True
  try:
   if a.notify_only:
    roots=sorted(p for p in (ROOT/'data/trade').glob('screener-*') if p.is_dir())
    if roots:
     for offset in range(len(roots)):
      index=(rotation+offset)%len(roots)
      if drain_one(roots[index]):rotation=index+1;break
     else:rotation+=1
   else:
    ok=main(['--exits-only'])==0
    index=ROOT/'data/trade/bots.html'
    if not index.exists() or time.time()-index.stat().st_mtime>=15:
     from tools.trade.page import write_all
     write_all(ROOT/'data/trade')
  except Exception as exc:print('fast monitor:',type(exc).__name__,flush=True);ok=False
  elapsed=time.monotonic()-started
  if not a.notify_only:
   path=ROOT/'data/trade/monitor-status.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(dict(updated_ms=int(time.time()*1000),duration_seconds=round(elapsed,3),interval_seconds=a.interval,ok=ok,**LAST_MONITOR_STATS)),encoding='utf-8');os.replace(tmp,path)
  print('monitor round',round(elapsed,3),'seconds; ok',ok,flush=True)
  if a.once:return 0 if ok else 1
  time.sleep(max(0,a.interval-elapsed))

if __name__=='__main__':raise SystemExit(run())

