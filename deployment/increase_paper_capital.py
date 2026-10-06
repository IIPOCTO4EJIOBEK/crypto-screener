"""Increase contributed PAPER capital, with SQL backups and durable flow events."""
from pathlib import Path
import json,shlex
from deploy_server import connect,run,put,HOST
ROOT=Path(__file__).resolve().parents[1]
FILES=['src/trade/capital_flow.py','tools/trade/screener_page.py','tools/trade/page.py','tools/trade/audit.py','tests/test_capital_flow.py']
TARGET=10000;OPERATION='paper-capital-10000-20261006'
WRITERS='screener-bots.timer screener-bots.service screener-monitor.service screener-funding.service screener-control.service'
def main():
 s=connect(HOST,'root');paused=False
 try:
  stamp=run(s,'date -u +%Y%m%dT%H%M%SZ',quiet=True).strip();backup='/opt/paper-capital-backup-'+stamp
  run(s,'install -d -m 700 -o screener -g screener '+backup,quiet=True)
  for name in FILES:
   dest='/opt/crypto-trade/'+name;save=backup+'/'+name
   run(s,'mkdir -p '+shlex.quote(save.rsplit('/',1)[0])+'; if test -f '+shlex.quote(dest)+'; then cp -p '+shlex.quote(dest)+' '+shlex.quote(save)+'; fi',quiet=True)
   put(s,dest,(ROOT/'wt-bots-srcdoc'/name).read_text('utf-8'),0o644)
  run(s,'chown screener:screener '+' '.join('/opt/crypto-trade/'+x for x in FILES),quiet=True)
  print(run(s,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_capital_flow.py tests/test_screener_bot.py tests/test_position_controls.py',quiet=True),flush=True)
  run(s,'systemctl stop '+WRITERS,quiet=True);paused=True
  cfg='/opt/crypto-trade/data/trade/screener-profiles.json'
  old=run(s,'cat '+cfg,quiet=True);profiles=json.loads(old);assert len(profiles)==7
  put(s,backup+'/screener-profiles.json',old,0o600)
  for args in profiles:
   if '--capital' in args:args[args.index('--capital')+1]=str(TARGET)
   else:args+=['--capital',str(TARGET)]
   assert args[args.index('--max-open')+1]=='200'
   assert args[args.index('--capital-fraction')+1]=='0.8'
   assert args[args.index('--max-drawdown')+1]=='0.25'
  code='''import pathlib,sqlite3,json,copy
from src.trade.atomic_store import read_state
from src.trade.ledger import Ledger
from src.trade.capital_flow import top_up,performance
root=pathlib.Path('/opt/crypto-trade/data/trade');backup=pathlib.Path(%r);results=[]
for folder in sorted(root.glob('screener-*')):
 if not (folder/'execution.db').exists():continue
 before=read_state(folder)
 with sqlite3.connect(folder/'execution.db') as a,sqlite3.connect(backup/(folder.name+'.db')) as b:a.backup(b)
 (backup/(folder.name+'-before.json')).write_text(json.dumps(before,ensure_ascii=False))
 result=top_up(Ledger(folder),%r,%r)
 after=read_state(folder)
 for field in ('positions','seen','attempts','cooldown','streak','pending_funding','start_equity','start_ts','day_pnl'):
  assert after.get(field)==before.get(field),(folder.name,field)
 amount=result['amount'] if result else 0
 assert abs(after['cash']-before['cash']-amount)<1e-7
 perf=performance(after['start_equity'],Ledger(folder).journal(),after['cash']+sum((1 if p['side']=='long' else -1)*p['qty']*((p.get('mark') or p['entry'])-p['entry']) for p in after['positions'].values()))
 assert perf['funded_capital']==%r
 results.append(dict(profile=folder.name,result=result,performance={k:v for k,v in perf.items() if k!='curve'},positions=len(after['positions'])))
print(json.dumps(results,ensure_ascii=False))
''' % (backup,TARGET,OPERATION,TARGET)
  result=json.loads(run(s,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -c '+shlex.quote(code),quiet=True))
  put(s,cfg+'.tmp',json.dumps(profiles,ensure_ascii=False,indent=2),0o640)
  run(s,'chown screener:screener '+cfg+'.tmp; mv '+cfg+'.tmp '+cfg,quiet=True)
  # Restart existing readers of page.py; no state schema change is needed.
  run(s,'systemctl start screener-monitor.service screener-funding.service screener-control.service screener-bots.timer',quiet=True);paused=False
  run(s,'systemctl start screener-bots.service',quiet=True)
  print('BACKUP',backup,flush=True)
  for p in result:print(p['profile'],'funded',p['performance']['funded_capital'],'flow',p['result']['amount'] if p['result'] else 0,'trading_pnl',round(p['performance']['pnl'],4),'positions',p['positions'],flush=True)
  (ROOT/'deployment/paper-capital-result.json').write_text(json.dumps({'backup':backup,'profiles':result},ensure_ascii=False,indent=2),'utf-8')
  print(run(s,'systemctl is-active screener-monitor.service screener-funding.service screener-control.service screener-bots.timer',quiet=True))
 finally:
  if paused:run(s,'systemctl start screener-monitor.service screener-funding.service screener-control.service screener-bots.timer',quiet=True)
  s.close()
if __name__=='__main__':main()
