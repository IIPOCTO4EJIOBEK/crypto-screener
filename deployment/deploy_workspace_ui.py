"""Reversible UI-only deployment; no bot settings or ledger writes."""
from pathlib import Path
import time
from deploy_server import connect, run, put, HOST

root = Path(__file__).resolve().parents[1]
stamp = time.strftime('%Y%m%d-%H%M%S')
client = connect(HOST, 'root')
try:
    for name in ['board.html', 'nav.js', 'navinject.py', 'workspace.css', 'workspace.js']:
        dest = '/opt/crypto-screener/tools/live/' + name
        run(client, f'if test -f {dest}; then cp -a {dest} {dest}.before-ui-{stamp}; fi', quiet=True)
        put(client, dest, (root/'screener-board/tools/live'/name).read_text('utf-8'), 0o644)
        run(client, 'chown screener:screener ' + dest, quiet=True)
    # The running nav injector has the old asset list; copy new assets explicitly now.
    run(client, 'cd /opt/crypto-screener && sudo -u screener .venv/bin/python -c "from pathlib import Path; from tools.live.navinject import ensure; ensure(Path(\'docs/live\'))"', quiet=True)
    run(client, 'cd /opt/crypto-screener && sudo -u screener .venv/bin/python -m tools.live.board --db data/screener.db --universe data/universe-turnover.json --html docs/live/board.html')
    run(client, 'systemctl is-active screener.service screener-monitor.service screener-books.service')
    print('UI installed; backup suffix before-ui-' + stamp + '; trading services kept running')
finally:
    client.close()
