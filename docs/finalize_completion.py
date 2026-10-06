from pathlib import Path
import json,shlex
from deploy_server import connect,run,put,HOST
r=Path(__file__).resolve().parents[1];s=connect(HOST,'root')
try:
 for rel in ['src/trade/execution_stats.py','tools/trade/diagnostics.py','tools/trade/load_check.py','tests/test_execution_stats.py','tests/test_reversal.py']:
  path='/opt/crypto-trade/'+rel;put(s,path,(r/'wt-bots-srcdoc'/rel).read_text('utf-8'),0o644);run(s,'chown screener:screener '+path,quiet=True)
 run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_reversal.py tests/test_recovery_worker.py tests/test_execution_stats.py tests/test_trend_display.py tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py -q')
 load=json.loads(run(s,'cd /opt/crypto-trade && .venv/bin/python -m tools.trade.load_check',quiet=True));(r/'deployment/load-check-vps-reversal.json').write_text(json.dumps(load,indent=2));print('Load with reversal enabled',json.dumps(load))
 run(s,'systemctl start screener-diagnostics.service screener-health.service screener-backup.service; systemctl show screener-health.service screener-backup.service -p Id -p Result; systemctl --failed --no-pager')
 code='''import json,pathlib,time
from src.trade.reversal import load
r=pathlib.Path('/opt/crypto-trade/data/trade');p=json.loads((r/'screener-profiles.json').read_text());assert all('--exit-on-opposite' in a and a[a.index('--max-open')+1]=='200' for a in p)
print('Reversal profiles',len(p));print('Fresh structural rows',len(load('/opt/crypto-screener/docs/live/trade-setups.json','/opt/crypto-screener/docs/live/kl',int(time.time()*1000))))
print('Recovery',json.loads((r/'recovery-status.json').read_text())['errors']);print('Monitor',json.loads((r/'monitor-status.json').read_text())['duration_seconds'])
'''
 run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code))
finally:s.close()
