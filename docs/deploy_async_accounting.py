from pathlib import Path
import time,shlex
from deploy_server import connect,run,put,HOST
root=Path(__file__).resolve().parents[1];stamp=time.strftime('%Y%m%d-%H%M%S');s=connect(HOST,'root')
names=['src/trade/entry_snapshot.py','src/trade/position_controls.py','src/trade/inbox.py','src/trade/atomic_store.py','src/trade/intraday.py','src/trade/funding_settlement.py','src/trade/photo_reports.py','tools/trade/screener_bot.py','tools/trade/screener_page.py','tools/trade/funding_worker.py','tests/test_control_quotes.py','tests/test_funding_settlement.py','tests/test_book_cache.py']
tests='tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py'
unit='''[Unit]
Description=Deferred paper funding accounting
After=network-online.target
Wants=network-online.target
[Service]
User=screener
WorkingDirectory=/opt/crypto-trade
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/crypto-trade/.env
ExecStart=/opt/crypto-trade/.venv/bin/python -m tools.trade.funding_worker
Restart=always
RestartSec=5
UMask=0027
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
'''
try:
 run(s,'systemctl is-active screener-books.service screener-monitor.service; systemctl show screener-bots.service -p Result')
 run(s,'systemctl start screener-backup.service; systemctl show screener-backup.service -p Result')
 run(s,'systemctl stop screener-bots.timer screener-bots.service screener-monitor.service screener-notifications.service',quiet=True)
 try:
  for name in names:
   dest='/opt/crypto-trade/'+name
   run(s,'if test -f '+dest+'; then cp -a '+dest+' '+dest+'.before-accounting-'+stamp+'; fi',quiet=True)
   put(s,dest,(root/'wt-bots-srcdoc'/name).read_text('utf-8'),0o644);run(s,'chown screener:screener '+dest,quiet=True)
  run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest '+tests+' -q')
  put(s,'/etc/systemd/system/screener-funding.service',unit,0o644)
  for service in ['screener-monitor','screener-bots']:
   run(s,'mkdir -p /etc/systemd/system/'+service+'.service.d',quiet=True)
   put(s,'/etc/systemd/system/'+service+'.service.d/accounting.conf','[Service]\nEnvironment=SCREENER_DEFER_FUNDING=1\nEnvironment=SCREENER_BOOK_DIR=/opt/crypto-trade/data/books\n',0o644)
  run(s,'systemctl daemon-reload; systemctl enable --now screener-funding.service',quiet=True)
 finally:run(s,'systemctl start screener-monitor.service screener-notifications.service screener-bots.timer',quiet=True)
 health=run(s,'cat /usr/local/sbin/screener-health',quiet=True)
 if 'funding-status.json' not in health:
  run(s,'cp -a /usr/local/sbin/screener-health /usr/local/sbin/screener-health.before-accounting-'+stamp,quiet=True)
  health+='\nassert subprocess.run(["systemctl","is-active","--quiet","screener-funding.service"]).returncode==0, "funding worker inactive"\nf=json.loads(pathlib.Path("/opt/crypto-trade/data/trade/funding-status.json").read_text())\nassert time.time()*1000-f["updated_ms"]<600000, "funding heartbeat stale"\nassert not f["errors"], "funding accounting requires retry"\n'
  put(s,'/usr/local/sbin/screener-health',health,0o755)
 run(s,'systemctl start screener-bots.service; systemctl show screener-bots.service -p Result -p ExecMainStatus; systemctl start screener-health.service; systemctl show screener-health.service -p Result; systemctl --failed --no-pager')
 code='''import json,time,pathlib,sqlite3
from src.trade.funding_settlement import pending
r=pathlib.Path('/opt/crypto-trade/data/trade')
for i in range(3):
 m=json.loads((r/'monitor-status.json').read_text());f=json.loads((r/'funding-status.json').read_text())
 print(json.dumps(dict(monitor_seconds=m['duration_seconds'],ok=m['ok'],busy=m.get('busy_profiles'),funding=f)),flush=True);time.sleep(5)
for db in sorted(r.glob('screener-*/execution.db')):
 with sqlite3.connect(db) as c:
  st=json.loads(c.execute('SELECT payload FROM state WHERE id=1').fetchone()[0]);assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  assert st.get('pending_funding',0)==len(pending(db.parent))
  print(db.parent.name,'SQL ok; pending',st.get('pending_funding',0),flush=True)
'''
 run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code))
 run(s,'systemctl start screener-backup.service; systemctl show screener-backup.service -p Result')
 print('Async accounting installed, code reserve',stamp)
finally:s.close()
