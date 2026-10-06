"""UI-only atomic publication; never edits trading state or configuration."""
from pathlib import Path
import time
from deploy_server import connect,run,put,HOST
base=Path(__file__).resolve().parents[1]
if not (base/'wt-bots-srcdoc').exists():base=base.parent
stamp=time.strftime('%Y%m%d-%H%M%S')
c=connect(HOST,'root')
try:
    files=[(base/'wt-bots-srcdoc/tools/trade'/n,'/opt/crypto-trade/tools/trade/'+n) for n in ('page.py','screener_page.py')]
    files += [(base/'screener-board/tools/live'/n,remote+n) for n in ('workspace.js','nav.js') for remote in ('/opt/crypto-screener/tools/live/','/opt/crypto-screener/docs/live/')]
    for source,dest in files:
        run(c,f'cp -a {dest} {dest}.before-bots-layout-{stamp}',quiet=True)
        put(c,dest+'.layout-tmp',source.read_text('utf-8'),0o644)
        run(c,f'chown screener:screener {dest}.layout-tmp && mv {dest}.layout-tmp {dest}',quiet=True)
    run(c,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -m compileall -q tools/trade/page.py tools/trade/screener_page.py',quiet=True)
    run(c,'systemctl restart screener-monitor.service',quiet=True)
    print('UI published; backup suffix before-bots-layout-'+stamp)
    run(c,'systemctl is-active screener-monitor.service screener.service screener-thesis.service screener-control.service')
finally:c.close()
