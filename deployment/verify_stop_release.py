from pathlib import Path
import json,shlex,urllib.request,base64
from deploy_server import connect,run,HOST
ROOT=Path(__file__).resolve().parents[1]
code='''import json,time,sqlite3,pathlib
from src.data.entry_safety import stop_guard
root=pathlib.Path('/opt/crypto-trade/data/trade');opens=[];photos=[]
for folder in root.glob('screener-*'):
 if not folder.is_dir():continue
 with sqlite3.connect(folder/'execution.db') as c:
  for raw, in c.execute('select payload from journal'):
   e=json.loads(raw)
   if e.get('kind')=='open' and e['ts']>=1791313657000:
    rules=e.get('rules',{});opens.append((folder.name,e['symbol'],e.get('formation'),e['tf'],stop_guard(e['entry'],e['stop'],e['tf'],rules.get('prior_atr')),rules.get('trigger_level')))
  for sent,error,raw in c.execute('select sent,last_error,media from outbox where media is not null'):
   m=json.loads(raw)
   if m.get('event')=='close' and m.get('when',0)>=1791313657000 and m.get('position',{}).get('event_reason','').startswith('Подтверждённый разворот'):
    photos.append((folder.name,m['position']['symbol'],sent,error))
  if folder.name=='screener-managed':print('report',c.execute('select sent,last_error from outbox where id=?',('stop-review-report-20261006',)).fetchone())
print('new_opens',len(opens),'volume_opens',sum(x[2]=='volume_splash' for x in opens),'guard_failures',[x for x in opens if x[4]],'missing_structural_anchor',[x for x in opens if x[2] in ('breakout','retest','structure_break') and not x[5]])
print('photos',photos)
r=pathlib.Path('/opt/crypto-screener/docs/live');d=json.loads((r/'trade-setups.json').read_text());print('setups_age',round(time.time()-d['built_unix'],2),'rows',len(d['signals']))
print('recovery',json.loads((root/'recovery-status.json').read_text())['errors']);m=json.loads((root/'monitor-status.json').read_text());print('monitor_ok',m['ok'],'seconds',m['duration_seconds'])
'''
def main():
 s=connect(HOST,'root')
 try:print(run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code),quiet=True))
 finally:s.close()
 cfg={}
 for line in (Path.home()/'.ssh/screener-web-access.txt').read_text('utf-8').splitlines():
  if ':' in line:k,v=line.split(':',1);cfg[k.strip().lower()]=v.strip()
 auth=base64.b64encode((cfg['login']+':'+cfg['password']).encode()).decode();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for path in ['/position-review.html','/STOP-REVIEW-20261006.md','/nav.js','/bots.html','/structures.html']:
  req=urllib.request.Request('https://vpn.markus.tw1.su'+path,headers={'Authorization':'Basic '+auth});r=opener.open(req,timeout=30);body=r.read().decode('utf-8');print('HTTPS',path,r.status,len(body))
  if path=='/position-review.html':assert 'Фиксированный отчёт' in body and 'FLUIDUSDT' in body
  if path=='/nav.js':assert '/position-review.html' in body
if __name__=='__main__':main()
