from pathlib import Path
import time,shlex
from deploy_server import connect,run,put,HOST
r=Path(__file__).resolve().parents[1];s=connect(HOST,'root');stamp=time.strftime('%Y%m%d-%H%M%S')
try:
 run(s,'systemctl stop screener-bots.timer screener-bots.service screener-monitor.service',quiet=True)
 try:
  for repo,remote,names in [('wt-bots-srcdoc','/opt/crypto-trade',['src/trade/trend_display.py','tools/trade/screener_page.py','tests/test_trend_display.py']),('screener-board','/opt/crypto-screener',['tools/live/nav.js'])]:
   for name in names:
    dest=remote+'/'+name;run(s,'if test -f '+dest+'; then cp -a '+dest+' '+dest+'.before-fluid-'+stamp+'; fi',quiet=True)
    put(s,dest,(r/repo/name).read_text('utf-8'),0o644);run(s,'chown screener:screener '+dest,quiet=True)
  run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_trend_display.py tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py -q')
 finally:run(s,'systemctl start screener-monitor.service screener-bots.timer',quiet=True)
 run(s,'systemctl start screener-bots.service; systemctl show screener-bots.service -p Result',quiet=False)
 run(s,'cp /opt/crypto-screener/tools/live/nav.js /opt/crypto-screener/docs/live/nav.js; chown screener:screener /opt/crypto-screener/docs/live/nav.js',quiet=True)
 code='''import json,pathlib
r=pathlib.Path('/opt/crypto-trade/data/trade/screener-alerts');x=json.loads((r/'bot.json').read_text());p=next(p for p in x['open'] if p['symbol']=='FLUIDUSDT')
print(json.dumps({k:p[k] for k in ['symbol','tf','side','trend_at_entry','trend_now','trend_conflict']}))
assert p['tf']=='1h' and p['side']=='long' and p['trend_at_entry']=='long'
assert 'тренд: на входе / сейчас' in (r/'bot.html').read_text()
print('Historical entry preserved, live trend shown separately')
'''
 run(s,'python3 -c '+shlex.quote(code))
 run(s,'systemctl start screener-health.service; systemctl show screener-health.service -p Result; systemctl --failed --no-pager')
 print('Display code reserve',stamp)
finally:s.close()
