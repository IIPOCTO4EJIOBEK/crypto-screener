from pathlib import Path
import shlex,json,urllib.request,base64
from deploy_server import connect,run,HOST
ROOT=Path(__file__).resolve().parents[1]
def main():
 s=connect(HOST,'root')
 try:
  code='''import json,pathlib,sqlite3
from tools.trade.audit import profile
root=pathlib.Path('/opt/crypto-trade/data/trade');out=[]
for folder in sorted(root.glob('screener-*')):
 if not (folder/'execution.db').is_file():continue
 a=profile(folder);assert a['reconciled'],(folder.name,a['cash_delta']);assert a['net_deposits']==9000
 doc=json.loads((folder/'bot.json').read_text());assert doc['funded_capital']==10000 and abs(doc['return_fraction'])<.5
 with sqlite3.connect(folder/'execution.db') as c:
  flows=[json.loads(r[0]) for r in c.execute('select payload from journal') if json.loads(r[0])['kind']=='capital_flow']
  notifications=[r for r in c.execute('select sent,last_error from outbox where text like ?',('%Виртуальное пополнение%',))]
 assert len(flows)==1
 out.append(dict(profile=folder.name,equity=a['equity'],trading_pnl=a['trading_pnl'],return_pct=a['return_pct'],cash_delta=a['cash_delta'],positions=a['positions'],notification=notifications))
print(json.dumps(out,ensure_ascii=False))
print('monitor',json.loads((root/'monitor-status.json').read_text())['ok'])
'''
  result=run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(code),quiet=True)
  print(result)
  (ROOT/'deployment/paper-capital-verification.txt').write_text(result,'utf-8')
  print(run(s,'systemctl start screener-health.service screener-backup.service; systemctl show screener-health.service screener-backup.service -p Id -p Result',quiet=True))
 finally:s.close()
 cfg={}
 for line in (Path.home()/'.ssh/screener-web-access.txt').read_text('utf-8').splitlines():
  if ':' in line:k,v=line.split(':',1);cfg[k.strip().lower()]=v.strip()
 auth=base64.b64encode((cfg['login']+':'+cfg['password']).encode()).decode();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 r=opener.open(urllib.request.Request('https://vpn.markus.tw1.su/bots.html',headers={'Authorization':'Basic '+auth}),timeout=30);body=r.read().decode('utf8');assert '10000.00' in body and 'доходность без пополнений' in body;print('HTTPS bots',r.status,'capital and adjusted return shown')
if __name__=='__main__':main()
