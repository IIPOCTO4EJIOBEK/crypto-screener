import json,time,shlex
from pathlib import Path
from deploy_server import connect,run,put,HOST
r=Path(__file__).resolve().parents[1];s=connect(HOST,'root');stamp=time.strftime('%Y%m%d-%H%M%S');path='/opt/crypto-trade/data/trade/screener-profiles.json'
try:
 run(s,'systemctl stop screener-bots.timer screener-bots.service screener-monitor.service',quiet=True)
 try:
  for rel in ['src/trade/reversal.py','src/trade/intraday.py','tools/trade/screener_bot.py','tools/trade/screener_page.py','tests/test_reversal.py','tests/test_screener_bot.py']:
   dest='/opt/crypto-trade/'+rel
   run(s,'if test -f '+dest+'; then cp -a '+dest+' '+dest+'.before-reversal-'+stamp+'; fi',quiet=True)
   put(s,dest,(r/'wt-bots-srcdoc'/rel).read_text('utf-8'),0o644);run(s,'chown screener:screener '+dest,quiet=True)
  run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_reversal.py tests/test_recovery_worker.py tests/test_execution_stats.py tests/test_trend_display.py tests/test_control_quotes.py tests/test_funding_settlement.py tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py -q')
  profiles=json.loads(run(s,'cat '+path,quiet=True));assert len(profiles)==7
  for args in profiles:
   if '--exit-on-opposite' not in args:args.append('--exit-on-opposite')
   assert args[args.index('--max-open')+1]=='200'
  run(s,'cp -a '+path+' '+path+'.before-reversal-'+stamp,quiet=True)
  tmp=path+'.reversal-tmp';put(s,tmp,json.dumps(profiles,ensure_ascii=False,indent=2)+'\n',0o640);run(s,'chown screener:screener '+tmp+' && mv '+tmp+' '+path,quiet=True)
 finally:run(s,'systemctl start screener-monitor.service screener-bots.timer',quiet=True)
 run(s,'systemctl start screener-bots.service screener-diagnostics.service; systemctl show screener-bots.service -p Result')
 print('Confirmed reversal enabled for seven profiles; reserve before-reversal-'+stamp)
finally:s.close()
