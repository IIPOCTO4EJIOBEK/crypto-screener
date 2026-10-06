"""Read consistent per-profile SQLite snapshots without stopping trading."""
from pathlib import Path
import json,shlex
from deploy_server import connect,run,HOST
ROOT=Path(__file__).resolve().parents[1]
CODE='''import json,sqlite3,pathlib,time
root=pathlib.Path('/opt/crypto-trade/data/trade');out={'updated_ms':int(time.time()*1000),'profiles':{}}
for folder in root.glob('screener-*'):
 path=folder/'execution.db'
 if not path.is_file():continue
 with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as c:
  c.execute('BEGIN')
  state=json.loads(c.execute('SELECT payload FROM state WHERE id=1').fetchone()[0])
  rows=[]
  for raw, in c.execute('SELECT payload FROM journal ORDER BY id'):
   r=json.loads(raw)
   if r['kind'] in ('open','close','partial','equity','skip','halt'):rows.append(r)
 out['profiles'][folder.name]={'state':state,'rows':rows}
print(json.dumps(out,ensure_ascii=False))
'''
def main():
 s=connect(HOST,'root')
 try:d=json.loads(run(s,'cd /opt/crypto-trade && .venv/bin/python -c '+shlex.quote(CODE),quiet=True))
 finally:s.close()
 (ROOT/'deployment/rule-evaluation-private.json').write_text(json.dumps(d,ensure_ascii=False),'utf-8')
 print('Profiles',len(d['profiles']),'rows',sum(len(p['rows']) for p in d['profiles'].values()))
if __name__=='__main__':main()
