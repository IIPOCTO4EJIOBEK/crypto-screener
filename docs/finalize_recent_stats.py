from pathlib import Path
from deploy_server import connect,run,put,HOST
r=Path(__file__).resolve().parents[1];s=connect(HOST,'root')
try:
 for name in ['src/trade/execution_stats.py','tools/trade/diagnostics.py','tests/test_execution_stats.py']:
  put(s,'/opt/crypto-trade/'+name,(r/'wt-bots-srcdoc'/name).read_text('utf-8'),0o644);run(s,'chown screener:screener /opt/crypto-trade/'+name,quiet=True)
 run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_recovery_worker.py tests/test_execution_stats.py tests/test_trend_display.py tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py -q; systemctl start screener-diagnostics.service')
finally:s.close()
