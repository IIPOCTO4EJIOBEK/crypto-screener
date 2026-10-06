from pathlib import Path
import shlex,time
from deploy_server import connect,run,put,HOST
s=connect(HOST,'root');root=Path(__file__).resolve().parents[1]
try:
 put(s,'/opt/crypto-trade/src/trade/book_cache.py',(root/'wt-bots-srcdoc/src/trade/book_cache.py').read_text('utf-8'),0o644)
 run(s,'chown screener:screener /opt/crypto-trade/src/trade/book_cache.py',quiet=True)
 health=run(s,'cat /usr/local/sbin/screener-health',quiet=True)
 if 'books/status.json' not in health:
  run(s,'cp -a /usr/local/sbin/screener-health /usr/local/sbin/screener-health.before-books-'+time.strftime('%Y%m%d-%H%M%S'),quiet=True)
  health+='\nimport subprocess\nassert subprocess.run(["systemctl","is-active","--quiet","screener-books.service"]).returncode==0, "book feed inactive"\nb=json.loads(pathlib.Path("/opt/crypto-trade/data/books/status.json").read_text())\nassert time.time()*1000-b["updated_ms"]<30000, "book heartbeat stale"\nassert b["symbols"]>0 and b["fresh"]>=b["symbols"]*.8, "book coverage below 80 percent"\n'
  put(s,'/usr/local/sbin/screener-health',health,0o755)
 run(s,'systemctl start screener-bots.service',quiet=True)
 run(s,'systemctl show screener-bots.service -p Result -p ExecMainStatus; systemctl start screener-health.service; systemctl show screener-health.service -p Result; systemctl --failed --no-pager')
 code='''import pathlib,json,time
from src.trade.entry_snapshot import prepare
from src.trade.intraday import BotState,Config
r=pathlib.Path('/opt/crypto-trade/data/books')
b=prepare([],BotState(cash=1000,peak=1000,start_equity=1000),Config(),book_root=r)
for i in range(3):
 d=json.loads((r/'status.json').read_text());m=json.loads(pathlib.Path('/opt/crypto-trade/data/trade/monitor-status.json').read_text())
 print(json.dumps(dict(books=d,monitor_seconds=m['duration_seconds'],monitor_ok=m['ok'],busy=m.get('busy_profiles'),btc_mid=b.mid('BTCUSDT'))),flush=True)
 time.sleep(5)
'''
 run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code))
 run(s,'systemctl start screener-backup.service; systemctl show screener-backup.service -p Result')
finally:s.close()
