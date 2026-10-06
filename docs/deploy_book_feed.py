from pathlib import Path
import shlex,time
from deploy_server import connect,run,put,HOST
root=Path(__file__).resolve().parents[1];stamp=time.strftime('%Y%m%d-%H%M%S');s=connect(HOST,'root')
unit='''[Unit]
Description=Public futures top20 depth for paper entries
After=network-online.target
Wants=network-online.target
[Service]
User=screener
WorkingDirectory=/opt/crypto-trade
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/crypto-trade/.venv/bin/python -m tools.trade.book_feed --universe /opt/crypto-screener/data/universe-turnover.json --out /opt/crypto-trade/data/books
Restart=always
RestartSec=5
UMask=0027
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
'''
try:
 run(s,'systemctl is-active screener-monitor.service screener-notifications.service; systemctl show screener-bots.service -p Result',quiet=False)
 for name in ['src/trade/book_cache.py','tools/trade/book_feed.py','tests/test_book_cache.py']:
  put(s,'/opt/crypto-trade/'+name,(root/'wt-bots-srcdoc'/name).read_text('utf-8'),0o644)
 run(s,'chown screener:screener /opt/crypto-trade/src/trade/book_cache.py /opt/crypto-trade/tools/trade/book_feed.py /opt/crypto-trade/tests/test_book_cache.py',quiet=True)
 put(s,'/etc/systemd/system/screener-books.service',unit,0o644)
 run(s,'systemctl daemon-reload; systemctl enable --now screener-books.service',quiet=True)
 # Verify actual transport before switching the entry worker. No orders or ledger writes here.
 code='''import time,json,pathlib
p=pathlib.Path('/opt/crypto-trade/data/books/status.json')
for i in range(35):
 if p.exists():
  d=json.loads(p.read_text())
  if d['fresh']>=max(1,d['symbols']*.8):print(json.dumps(d));break
 time.sleep(1)
else:raise RuntimeError('depth feed not sufficiently fresh')
'''
 run(s,'python3 -c '+shlex.quote(code))
 run(s,'systemctl stop screener-bots.timer screener-bots.service',quiet=True)
 try:
  for name in ['src/trade/entry_snapshot.py','tools/trade/screener_bot.py']:
   dest='/opt/crypto-trade/'+name
   run(s,'cp -a '+dest+' '+dest+'.before-books-'+stamp,quiet=True)
   put(s,dest,(root/'wt-bots-srcdoc'/name).read_text('utf-8'),0o644);run(s,'chown screener:screener '+dest,quiet=True)
  run(s,'cd /opt/crypto-trade && .venv/bin/python -m pytest tests/test_book_cache.py tests/test_retest_confirmation.py tests/test_fast_monitor.py tests/test_trade.py tests/test_screener_bot.py tests/test_position_controls.py tests/test_manual_levels.py tests/test_levels_api.py tests/test_trade_plan.py tests/test_dual_study.py -q')
  run(s,'mkdir -p /etc/systemd/system/screener-bots.service.d',quiet=True)
  put(s,'/etc/systemd/system/screener-bots.service.d/books.conf','[Service]\nEnvironment=SCREENER_BOOK_DIR=/opt/crypto-trade/data/books\n',0o644)
  run(s,'systemctl daemon-reload',quiet=True)
 finally:run(s,'systemctl start screener-bots.timer',quiet=True)
 run(s,'systemctl start screener-health.service; systemctl show screener-health.service -p Result; systemctl --failed --no-pager')
 print('WS entries installed; code backup',stamp)
finally:s.close()
