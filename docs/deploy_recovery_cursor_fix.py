from pathlib import Path
import time,shlex,json
from deploy_server import connect,run,put,HOST
r=Path(__file__).resolve().parents[1];s=connect(HOST,'root');stamp=time.strftime('%Y%m%d-%H%M%S')
try:
 run(s,'systemctl stop screener-bots.timer screener-bots.service screener-monitor.service screener-recovery.service',quiet=True)
 try:
  for rel in ['src/trade/monitor_market.py','tests/test_recovery_worker.py']:
   dest='/opt/crypto-trade/'+rel
   run(s,'cp -a '+dest+' '+dest+'.before-cursor-'+stamp,quiet=True)
   put(s,dest,(r/'wt-bots-srcdoc'/rel).read_text('utf-8'),0o644)
   run(s,'chown screener:screener '+dest,quiet=True)
  run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_recovery_worker.py tests/test_fast_monitor.py -q')
 finally:run(s,'systemctl start screener-monitor.service screener-recovery.service screener-bots.timer',quiet=True)
 time.sleep(8)
 code='''import json,pathlib,time
r=pathlib.Path('/opt/crypto-trade/data/trade')
for name in ['monitor','recovery']:
 d=json.loads((r/(name+'-status.json')).read_text());print(name,json.dumps(d));assert time.time()*1000-d['updated_ms']<30000
 if name=='recovery':assert not d['errors']
 else:
  assert d['ok'] and not d['busy_profiles']
  assert all(v!='waiting-recovery' for v in d['market_sources'].values())
'''
 run(s,'python3 -c '+shlex.quote(code))
 run(s,'systemctl start screener-health.service screener-backup.service; systemctl show screener-health.service screener-backup.service -p Id -p Result; systemctl --failed --no-pager')
 print('Reserve before-cursor-'+stamp)
finally:s.close()
