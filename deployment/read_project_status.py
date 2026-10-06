"""Read-only current status; no services, balances or reports are changed."""
from pathlib import Path
import json,shlex
from deploy_server import connect,run,HOST
ROOT=Path(__file__).resolve().parents[1]
CODE='''import pathlib,sqlite3,json,time
root=pathlib.Path('/opt/crypto-trade/data/trade');now=int(time.time()*1000);out={'updated_ms':now,'profiles':[]}
for folder in sorted(root.glob('screener-*')):
 path=folder/'execution.db'
 if not path.is_file():continue
 with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as c:
  c.execute('BEGIN');st=json.loads(c.execute('select payload from state where id=1').fetchone()[0]);flows=[];closes=[]
  for raw, in c.execute('select payload from journal order by id'):
   row=json.loads(raw)
   if row['kind']=='capital_flow':flows.append(row)
   if row['kind']=='close' and row.get('qty',0)*row.get('entry',0)>1e-6:closes.append(row)
  pending=c.execute('select count(*) from outbox where sent=0').fetchone()[0]
  errors=c.execute('select count(*) from outbox where sent=0 and last_error is not null').fetchone()[0]
 positions=[p for p in st['positions'].values() if p['qty']*p['entry']>1e-6]
 eq=st['cash']+sum((1 if p['side']=='long' else -1)*p['qty']*((p.get('mark') or p['entry'])-p['entry']) for p in positions)
 funded=st['start_equity']+sum(f['amount'] for f in flows);since=max((f['ts'] for f in flows),default=0)
 new=[r for r in closes if r.get('opened_ms',0)>=since];settled=[r for r in new if not r.get('funding_pending')]
 out['profiles'].append(dict(profile=folder.name,equity=eq,funded=funded,trading_pnl=eq-funded,open=len(positions),closed_after_deposit=len(new),settled_after_deposit=len(settled),net_after_deposit=sum(r['pnl']-r.get('fee',0)-(r.get('funding') or 0) for r in settled),stops_after_deposit=sum(r['reason']=='stop' for r in new),notify_pending=pending,notify_errors=errors,halted=(folder/'HALT').exists(),paused=(folder/'PAUSE').exists()))
for name in ('monitor','recovery','thesis','funding'):
 try:
  data=json.loads((root/(name+'-status.json')).read_text());out[name]={k:data[k] for k in ('updated_ms','duration_seconds','ok','busy_profiles','errors','pending','missing','positions') if k in data};out[name]['age_seconds']=(now-data['updated_ms'])/1000
 except (OSError,ValueError,KeyError):out[name]={'unavailable':True}
out['screener_analysis_age_seconds']=time.time()-json.loads(pathlib.Path('/opt/crypto-screener/docs/live/trade-setups.json').read_text())['built_unix']
print(json.dumps(out,ensure_ascii=False))
'''
def main():
 s=connect(HOST,'root')
 try:
  data=json.loads(run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(CODE),quiet=True))
  units=run(s,'systemctl is-active screener.service screener-monitor.service screener-thesis.service screener-recovery.service screener-funding.service screener-control.service screener-bots.timer; systemctl --failed --no-pager; systemctl show screener-health.service screener-backup.service -p Id -p Result',quiet=True)
 finally:s.close()
 (ROOT/'deployment/project-status-private.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')
 print(json.dumps(data,ensure_ascii=False,indent=2));print(units)
if __name__=='__main__':main()
