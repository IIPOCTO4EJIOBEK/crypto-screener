"""Read-only HTTPS checks; credentials stay outside project and output."""
from pathlib import Path
import requests

cfg = {}
for line in (Path.home()/'.ssh/screener-web-access.txt').read_text('utf-8').splitlines():
    if ':' in line:
        key, value = line.split(':', 1)
        cfg[key.strip().lower()] = value.strip()
session = requests.Session()
session.trust_env = False
session.auth = (cfg['login'], cfg['password'])
for path, expected in [('workspace.css', 'scrollbar-gutter'), ('workspace.js', 'workspace-ui'), ('nav.js', 'workspace.css'), ('', 'chart-active'), ('bots.html', 'iframe')]:
    response = session.get('https://vpn.markus.tw1.su/'+path, timeout=20)
    response.raise_for_status()
    assert expected in response.text, path
    print(path or '/', response.status_code, 'content verified')
