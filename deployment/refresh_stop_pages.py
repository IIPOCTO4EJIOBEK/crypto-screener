from deploy_server import connect,run,HOST
import shlex
s=connect(HOST,'root')
try:
 print(run(s,'journalctl -u screener.service --since "10 minutes ago" -n 8 --no-pager',quiet=True))
 print(run(s,'cd /opt/crypto-screener && sudo -u screener .venv/bin/python -m tools.live.structures --db data/screener.db --universe data/universe-turnover.json --html docs/live/structures.html --json docs/live/structures.json >/tmp/stop-review-structures.log && sudo -u screener .venv/bin/python -m tools.live.board --db data/screener.db --universe data/universe-turnover.json --html docs/live/board.html',quiet=True))
 print(run(s,"cd /opt/crypto-trade && .venv/bin/python -c "+shlex.quote("import json,pathlib;ps=json.loads(pathlib.Path('data/trade/screener-profiles.json').read_text());print([(p[p.index('--name')+1],'--notify' in p) for p in ps])"),quiet=True))
finally:s.close()
