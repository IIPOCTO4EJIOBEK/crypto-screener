from pathlib import Path
import json,time,shlex
from deploy_server import connect,run,put,HOST
r=Path(__file__).resolve().parents[1];s=connect(HOST,'root');stamp=time.strftime('%Y%m%d-%H%M%S')
trade=['tools/trade/recovery_worker.py','tools/trade/book_feed.py','src/trade/monitor_market.py','src/trade/intraday.py','src/trade/execution_stats.py','tools/trade/diagnostics.py','tools/trade/load_check.py','tests/test_recovery_worker.py','tests/test_execution_stats.py','tests/test_screener_bot.py']
board=['tools/live/klines.py','tests/test_position_feed.py']
try:
 run(s,'systemctl stop screener-bots.timer screener-bots.service screener-monitor.service screener-recovery.service screener-books.service screener.service',quiet=True)
 try:
  for repo,base,names in [('wt-bots-srcdoc','/opt/crypto-trade',trade),('screener-board','/opt/crypto-screener',board)]:
   for rel in names:
    dest=base+'/'+rel
    run(s,'if test -f '+dest+'; then cp -a '+dest+' '+dest+'.before-completion-'+stamp+'; fi',quiet=True)
    put(s,dest,(r/repo/rel).read_text('utf-8'),0o644);run(s,'chown screener:screener '+dest,quiet=True)
  run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_recovery_worker.py tests/test_execution_stats.py tests/test_trend_display.py tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py -q')
  run(s,'cd /opt/crypto-screener && .venv/bin/python -m pytest tests/test_position_feed.py tests/test_alert_freshness.py -q')
  for unit in ['screener','screener-books']:
   folder='/etc/systemd/system/'+unit+'.service.d';run(s,'mkdir -p '+folder,quiet=True)
   put(s,folder+'/positions.conf','[Service]\nEnvironment=SCREENER_POSITION_SYMBOLS=/opt/crypto-trade/data/trade/position-symbols.json\n',0o644)
  run(s,'systemctl daemon-reload; systemctl start screener-recovery.service',quiet=True)
 finally:run(s,'systemctl start screener.service screener-books.service screener-monitor.service screener-bots.timer',quiet=True)
 run(s,'cd /opt/crypto-trade && .venv/bin/python -m tools.trade.load_check',quiet=True)
 result=json.loads(run(s,'cd /opt/crypto-trade && .venv/bin/python -m tools.trade.load_check',quiet=True));(r/'deployment/load-check-vps.json').write_text(json.dumps(result,indent=2));print('VPS load',json.dumps(result))
 run(s,'systemctl start screener-diagnostics.service',quiet=True)
 print('Installed; reserve before-completion-'+stamp)
finally:s.close()
