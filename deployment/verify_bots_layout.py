"""Read-only live HTML and script verification; no command endpoint is called."""
from pathlib import Path
from html.parser import HTMLParser
import subprocess,tempfile,requests
class Frames(HTMLParser):
    def __init__(self):super().__init__();self.frames=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='iframe' and a.get('class')=='bot':self.frames.append(a)
cfg={}
for line in (Path.home()/'.ssh/screener-web-access.txt').read_text('utf-8').splitlines():
    if ':' in line:
        k,v=line.split(':',1);cfg[k.strip().lower()]=v.strip()
session=requests.Session();session.trust_env=False;session.auth=(cfg['login'],cfg['password'])
r=session.get('https://vpn.markus.tw1.su/bots.html',timeout=30);r.raise_for_status()
html=r.content.decode('utf-8');parser=Frames();parser.feed(html)
assert len(parser.frames)==7
import re
scripts=re.findall(r'<script>(.*?)</script>',html,re.S)
for frame in parser.frames:
    assert frame.get('data-autosize')=='content'
    doc=frame['srcdoc']
    assert doc.index('<h2>Открытые позиции')<doc.index('<summary>Правила')
    assert doc.count('<details ')==doc.count('</details>')==3
    assert 'bot-page-end' in doc and 'wrap positions' in doc and 'PnL USDT' in doc
    scripts+=re.findall(r'<script>(.*?)</script>',doc,re.S)
with tempfile.TemporaryDirectory() as tmp:
    for i,script in enumerate(scripts):
        path=Path(tmp)/f'check-{i}.js';path.write_text(script,encoding='utf-8')
        subprocess.run(['node','--check',str(path)],check=True,capture_output=True)
for asset,marker in [('workspace.js','iframe.bot:not([data-autosize])'),('nav.js','20261006-2')]:
    r=session.get('https://vpn.markus.tw1.su/'+asset,timeout=20);r.raise_for_status();assert marker in r.content.decode('utf-8')
print(f'HTTPS 200; all {len(parser.frames)} profiles use new layout; {len(scripts)} generated scripts parse; assets current')
