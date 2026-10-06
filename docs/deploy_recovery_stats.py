from pathlib import Path
import time,shlex
from deploy_server import connect,run,put,HOST
r=Path(__file__).resolve().parents[1];s=connect(HOST,'root');stamp=time.strftime('%Y%m%d-%H%M%S')
tests='tests/test_recovery_worker.py tests/test_execution_stats.py tests/test_trend_display.py tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py'
def unit(module,description,oneshot=False):
 return '[Unit]\nDescription='+description+'\nAfter=network-online.target\n[Service]\nUser=screener\nWorkingDirectory=/opt/crypto-trade\nEnvironment=PYTHONUNBUFFERED=1\nEnvironmentFile=/opt/crypto-trade/.env\nExecStart=/opt/crypto-trade/.venv/bin/python -m '+module+'\n'+('Type=oneshot\n' if oneshot else 'Restart=always\nRestartSec=5\n')+'UMask=0027\nNoNewPrivileges=true\n[Install]\nWantedBy=multi-user.target\n'
try:
 run(s,'systemctl stop screener-bots.timer screener-bots.service screener-monitor.service',quiet=True)
 try:
  for name in ['src/trade/monitor_market.py','src/trade/execution_stats.py','tools/trade/screener_bot.py','tools/trade/recovery_worker.py','tools/trade/control_worker.py','tools/trade/diagnostics.py','tests/test_recovery_worker.py','tests/test_execution_stats.py']:
   dest='/opt/crypto-trade/'+name;run(s,'if test -f '+dest+'; then cp -a '+dest+' '+dest+'.before-recovery-'+stamp+'; fi',quiet=True)
   put(s,dest,(r/'wt-bots-srcdoc'/name).read_text('utf-8'),0o644);run(s,'chown screener:screener '+dest,quiet=True)
  run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest '+tests+' -q')
  for name,module in [('recovery','tools.trade.recovery_worker'),('control','tools.trade.control_worker'),('diagnostics','tools.trade.diagnostics')]:put(s,'/etc/systemd/system/screener-'+name+'.service',unit(module,'Paper screener '+name,name=='diagnostics'),0o644)
  put(s,'/etc/systemd/system/screener-diagnostics.timer','[Unit]\nDescription=Paper execution statistics each minute\n[Timer]\nOnBootSec=10\nOnUnitActiveSec=60\n[Install]\nWantedBy=timers.target\n',0o644)
  for service in ['screener-monitor','screener-bots']:
   put(s,'/etc/systemd/system/'+service+'.service.d/recovery.conf','[Service]\nEnvironment=SCREENER_RECOVERY_DIR=/opt/crypto-trade/data/minute-recovery\nEnvironment=SCREENER_CONTROL_WORKER=1\n',0o644)
  run(s,'systemctl daemon-reload; systemctl enable --now screener-recovery.service screener-control.service screener-diagnostics.timer; systemctl start screener-diagnostics.service',quiet=True)
 finally:run(s,'systemctl start screener-monitor.service screener-bots.timer',quiet=True)
 for name in ['execution-stats.html','execution-stats.json']:
  run(s,'ln -sfn /opt/crypto-trade/data/trade/'+name+' /opt/crypto-screener/docs/live/'+name,quiet=True)
 put(s,'/opt/crypto-screener/tools/live/nav.js',(r/'screener-board/tools/live/nav.js').read_text('utf-8'),0o644)
 run(s,'cp /opt/crypto-screener/tools/live/nav.js /opt/crypto-screener/docs/live/nav.js; chown screener:screener /opt/crypto-screener/tools/live/nav.js /opt/crypto-screener/docs/live/nav.js',quiet=True)
 health=run(s,'cat /usr/local/sbin/screener-health',quiet=True)
 if 'recovery-status.json' not in health:
  run(s,'cp -a /usr/local/sbin/screener-health /usr/local/sbin/screener-health.before-recovery-'+stamp,quiet=True)
  health+='\nfor name in ("recovery","control"):\n assert subprocess.run(["systemctl","is-active","--quiet","screener-"+name+".service"]).returncode==0, name+" worker inactive"\n d=json.loads(pathlib.Path("/opt/crypto-trade/data/trade/"+name+"-status.json").read_text())\n assert time.time()*1000-d["updated_ms"]<600000, name+" heartbeat stale"\n assert not d.get("errors") and d.get("ok",True), name+" worker failure"\n'
  put(s,'/usr/local/sbin/screener-health',health,0o755)
 code='''import time,json,pathlib
r=pathlib.Path('/opt/crypto-trade/data/trade')
for i in range(3):
 print(json.dumps({n:json.loads((r/(n+'-status.json')).read_text()) for n in ['monitor','recovery','control']}),flush=True);time.sleep(5)
stats=json.loads((r/'execution-stats.json').read_text());print('Statistics profiles:',len(stats['profiles']))
for name,data in stats['profiles'].items():print(name,'closed',sum(r['closed'] for r in data['results']),'top reasons',data['reasons'][:2],flush=True)
'''
 run(s,'python3 -c '+shlex.quote(code))
 run(s,'systemctl start screener-bots.service; systemctl show screener-bots.service -p Result; systemctl start screener-health.service screener-backup.service; systemctl show screener-health.service screener-backup.service -p Result; systemctl --failed --no-pager')
 print('Recovery/statistics installed; reserve',stamp)
finally:s.close()
