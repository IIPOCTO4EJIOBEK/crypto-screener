"""Read-only runtime evidence, excluding credentials and raw journals from Git."""
from deploy_server import connect,run,HOST
from pathlib import Path
import shlex,json
ROOT=Path(__file__).resolve().parents[1]
code='''import json,time,pathlib
from src.trade.atomic_store import read_state
from src.trade.ledger import Ledger
from tools.trade.position_audit import audit
root=pathlib.Path('/opt/crypto-trade/data/trade');now=int(time.time()*1000)
out={'updated_ms':now,'audit':audit(root,'/opt/crypto-screener/docs/live/kl','/opt/crypto-screener/docs/live/trend-now.json'),'events':[]}
for folder in root.glob('screener-*'):
 if not folder.is_dir():continue
 for e in Ledger(folder).journal():
  if e.get('kind')=='close' and e.get('ts',e.get('t',0))>now-1800000:
   out['events'].append(dict(profile=folder.name,**e))
out['thesis']=json.loads((root/'thesis-status.json').read_text())
for name,path in [('monitor',root/'monitor-status.json'),('recovery',root/'recovery-status.json')]:
 try:out[name]=json.loads(path.read_text())
 except (OSError,ValueError):pass
print(json.dumps(out,ensure_ascii=False))
'''
def main():
 s=connect(HOST,'root')
 try:
  data=json.loads(run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code),quiet=True))
  (ROOT/'deployment/stop-review-after.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')
  print('positions',sum(not p['dust'] for p in data['audit']['positions']),'gaps',len(data['audit']['candle_gaps']))
  print('thesis',data['thesis']['positions'],len(data['thesis']['signals']),data['thesis']['missing'])
  print('events',[(e['profile'],e.get('symbol'),e.get('reason'),e.get('exit'),e.get('pnl'),e.get('manual_reason')) for e in data['events'] if e.get('reason')=='invalidation'])
  print('monitor',data.get('monitor'));print('recovery',data.get('recovery'))
  print(run(s,'journalctl -u screener-thesis.service -u screener-monitor.service -u screener-recovery.service --since "3 minutes ago" -n 14 --no-pager',quiet=True))
 finally:s.close()
if __name__=='__main__':main()
