"""Back up code/SQL and deploy confirmed paper-entry and invalidation checks."""
from pathlib import Path
import json,shlex
from deploy_server import connect,run,put,HOST
ROOT=Path(__file__).resolve().parents[1]
TRADE=['src/analysis/formations.py','src/data/trade_setups.py','src/data/entry_safety.py','src/trade/confirmation.py','src/trade/intraday.py','src/trade/monitor_market.py','src/trade/reversal.py','src/trade/thesis.py','tools/trade/recovery_worker.py','tools/trade/screener_bot.py','tools/trade/thesis_worker.py','tools/trade/position_audit.py','tests/test_thesis.py','tests/test_recovery_worker.py','tests/test_trade_plan.py']
BOARD=['src/analysis/formations.py','src/data/trade_setups.py','src/data/entry_safety.py','tools/live/board.py','tools/live/structures.py']
TESTS='tests/test_thesis.py tests/test_recovery_worker.py tests/test_trade_plan.py tests/test_fast_monitor.py tests/test_reversal.py tests/test_retest_confirmation.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_control_quotes.py tests/test_book_cache.py tests/test_execution_stats.py tests/test_funding_settlement.py'
UNIT='''[Unit]
Description=Paper position thesis invalidation publisher
After=screener.service
[Service]
User=screener
WorkingDirectory=/opt/crypto-trade
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/crypto-trade/.venv/bin/python -m tools.trade.thesis_worker
Restart=always
RestartSec=5
UMask=0027
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
'''
def main():
 s=connect(HOST,'root');paused=False;stamp=''
 try:
  stamp=run(s,'date -u +%Y%m%dT%H%M%SZ',quiet=True).strip();backup='/opt/stop-review-backup-'+stamp
  run(s,'install -d -m 700 '+backup,quiet=True)
  # Take a diagnostic snapshot before the deployment, without changing positions.
  put(s,'/opt/crypto-trade/tools/trade/position_audit.py',(ROOT/'wt-bots-srcdoc/tools/trade/position_audit.py').read_text('utf-8'),0o644)
  data=run(s,'cd /opt/crypto-trade && .venv/bin/python -m tools.trade.position_audit',quiet=True)
  (ROOT/'deployment/stop-review-before.json').write_text(data,'utf-8')
  for app,files in [('crypto-trade',TRADE),('crypto-screener',BOARD)]:
   for name in files:
    dest='/opt/'+app+'/'+name;save=backup+'/'+app+'/'+name
    run(s,'mkdir -p '+shlex.quote(str(Path(save).parent).replace('\\','/'))+'; if test -f '+shlex.quote(dest)+'; then cp -p '+shlex.quote(dest)+' '+shlex.quote(save)+'; fi',quiet=True)
  run(s,'systemctl stop screener-bots.timer screener-bots.service screener-monitor.service screener-recovery.service screener.service',quiet=True);paused=True
  code="""import sqlite3,pathlib
base=pathlib.Path('/opt/crypto-trade/data/trade');target=pathlib.Path(%r)/'ledgers';target.mkdir()
for p in base.glob('screener-*/execution.db'):
 a=sqlite3.connect(p);b=sqlite3.connect(target/(p.parent.name+'.db'));a.backup(b);b.close();a.close()
""" % backup
  run(s,'/opt/crypto-trade/.venv/bin/python -c '+shlex.quote(code),quiet=True)
  for app,local,files in [('crypto-trade','wt-bots-srcdoc',TRADE),('crypto-screener','screener-board',BOARD)]:
   for name in files:put(s,'/opt/'+app+'/'+name,(ROOT/local/name).read_text('utf-8'),0o644)
   run(s,'chown screener:screener '+' '.join(shlex.quote('/opt/'+app+'/'+n) for n in files),quiet=True)
  print(run(s,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -m pytest -q '+TESTS,quiet=True),flush=True)
  print(run(s,'cd /opt/crypto-screener && sudo -u screener .venv/bin/python -m pytest -q tests/test_position_feed.py tests/test_alert_freshness.py',quiet=True),flush=True)
  put(s,'/etc/systemd/system/screener-thesis.service',UNIT,0o644)
  run(s,'systemctl daemon-reload; systemctl enable --now screener-thesis.service; systemctl start screener.service screener-recovery.service screener-monitor.service screener-bots.timer',quiet=True);paused=False
  print('BACKUP',backup,flush=True)
  print(run(s,'systemctl is-active screener.service screener-thesis.service screener-recovery.service screener-monitor.service screener-bots.timer',quiet=True),flush=True)
  (ROOT/'deployment/stop-review-deployment.json').write_text(json.dumps({'backup':backup,'stamp':stamp,'files':TRADE+BOARD}),'utf-8')
 except Exception:
  # Restore only deployed sources on failure. Never replace live trading data.
  if paused:
   for app,files in [('crypto-trade',TRADE),('crypto-screener',BOARD)]:
    for name in files:
     save='/opt/stop-review-backup-'+stamp+'/'+app+'/'+name;dest='/opt/'+app+'/'+name
     run(s,'if test -f '+shlex.quote(save)+'; then cp -p '+shlex.quote(save)+' '+shlex.quote(dest)+'; fi',quiet=True)
   run(s,'systemctl start screener.service screener-recovery.service screener-monitor.service screener-bots.timer',quiet=True)
  raise
 finally:s.close()
if __name__=='__main__':main()
