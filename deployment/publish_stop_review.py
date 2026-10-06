"""Publish the audit, update navigation, and verify the paper runtime."""
from pathlib import Path
import shlex,json
from deploy_server import connect,run,put,HOST
ROOT=Path(__file__).resolve().parents[1]
def main():
 s=connect(HOST,'root')
 try:
  stamp='stop-review-20261006'
  for dest,source in [('/opt/crypto-trade/tools/trade/position_audit.py','wt-bots-srcdoc/tools/trade/position_audit.py'),('/opt/crypto-screener/tools/live/nav.js','screener-board/tools/live/nav.js'),('/opt/crypto-screener/docs/live/nav.js','screener-board/tools/live/nav.js'),('/opt/crypto-screener/docs/live/position-review.html','deployment/published/position-review.html'),('/opt/crypto-screener/docs/live/STOP-REVIEW-20261006.md','wt-bots-srcdoc/docs/STOP-REVIEW-20261006.md')]:
   run(s,'if test -f '+shlex.quote(dest)+'; then cp -p '+shlex.quote(dest)+' '+shlex.quote(dest+'.before-'+stamp)+'; fi',quiet=True)
   put(s,dest+'.tmp',(ROOT/source).read_text('utf-8'),0o644)
   run(s,'chown screener:screener '+shlex.quote(dest+'.tmp')+'; mv '+shlex.quote(dest+'.tmp')+' '+shlex.quote(dest),quiet=True)
  # The loop rebuilds structures/board each round. Verify its published artifacts.
  code='''import json,time,pathlib,sqlite3
r=pathlib.Path('/opt/crypto-screener/docs/live');p=r/'trade-setups.json';d=json.loads(p.read_text());forms=[]
print('setups_age_sec',round(time.time()-d['built_unix'],1),'signals',len(d['signals']))
for row in d['signals']:
 if row.get('kind') in ('breakout','structure_break','retest'):forms.append(row)
print('structural_with_exact_anchor',sum(bool(x.get('trigger_level')) for x in forms),'/',len(forms))
root=pathlib.Path('/opt/crypto-trade/data/trade')
for folder in root.glob('screener-*'):
 if not folder.is_dir():continue
 with sqlite3.connect(folder/'execution.db') as c:
  photos=[]
  for sent,error,raw in c.execute('select sent,last_error,media from outbox where media is not null'):
   m=json.loads(raw)
   if m.get('event')=='close' and m.get('when',0)>=1791313657000 and m.get('position',{}).get('event_reason','').startswith('Подтверждённый разворот'):
    photos.append((m['position']['symbol'],sent,error))
  if photos:print('exit_photos',folder.name,photos)
'''
  print(run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code),quiet=True))
  print(run(s,'systemctl start screener-health.service; systemctl show screener-health.service -p Result; systemctl --failed --no-pager',quiet=True))
 finally:s.close()
if __name__=='__main__':main()
