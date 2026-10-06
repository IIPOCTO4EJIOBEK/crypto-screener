from pathlib import Path
from deploy_server import connect,run,put,HOST
import shlex
s=connect(HOST,'root');root=Path(__file__).resolve().parents[1]
try:
 for name in ['src/trade/funding_settlement.py','tests/test_funding_settlement.py']:
  put(s,'/opt/crypto-trade/'+name,(root/'wt-bots-srcdoc'/name).read_text('utf-8'),0o644);run(s,'chown screener:screener /opt/crypto-trade/'+name,quiet=True)
 run(s,'systemctl restart screener-funding.service',quiet=True)
 run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py -q')
 code='''import time,json,pathlib
from src.data.market import binance_funding_history
x=binance_funding_history('BTCUSDT',int(time.time()*1000)-86400000,int(time.time()*1000))
print('Public funding history retrieved:',len(x),'records; no position writes')
r=pathlib.Path('/opt/crypto-trade/data/trade')
print(json.dumps(json.loads((r/'funding-status.json').read_text())))
'''
 run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code))
 run(s,'systemctl start screener-health.service; systemctl show screener-health.service -p Result; systemctl --failed --no-pager')
finally:s.close()
