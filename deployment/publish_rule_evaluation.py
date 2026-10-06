"""Publish arithmetic corrections and the authorised Telegram audit report."""
from pathlib import Path
import shlex,urllib.request,base64
from deploy_server import connect,run,put,HOST
ROOT=Path(__file__).resolve().parents[1]
def main():
 s=connect(HOST,'root')
 try:
  files=[('/opt/crypto-trade/src/trade/rule_evaluation.py','wt-bots-srcdoc/src/trade/rule_evaluation.py'),('/opt/crypto-trade/tests/test_rule_evaluation.py','wt-bots-srcdoc/tests/test_rule_evaluation.py'),('/opt/crypto-trade/tools/trade/position_audit.py','wt-bots-srcdoc/tools/trade/position_audit.py'),('/opt/crypto-screener/docs/live/rule-evaluation.html','deployment/published/rule-evaluation.html'),('/opt/crypto-screener/docs/live/RULE-EVALUATION-20261006.md','wt-bots-srcdoc/docs/RULE-EVALUATION-20261006.md'),('/opt/crypto-screener/docs/live/position-review.html','deployment/published/position-review.html'),('/opt/crypto-screener/docs/live/STOP-REVIEW-20261006.md','wt-bots-srcdoc/docs/STOP-REVIEW-20261006.md'),('/opt/crypto-screener/docs/live/nav.js','screener-board/tools/live/nav.js'),('/opt/crypto-screener/tools/live/nav.js','screener-board/tools/live/nav.js')]
  for dest,source in files:
   run(s,'if test -f '+shlex.quote(dest)+'; then cp -p '+shlex.quote(dest)+' '+shlex.quote(dest+'.before-evaluation-20261006')+'; fi',quiet=True)
   put(s,dest+'.tmp',(ROOT/source).read_text('utf-8'),0o644)
   run(s,'chown screener:screener '+shlex.quote(dest+'.tmp')+'; mv '+shlex.quote(dest+'.tmp')+' '+shlex.quote(dest),quiet=True)
  print(run(s,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rule_evaluation.py',quiet=True))
  message='Расчёт новых правил, срез 06.10 22:24:55 МСК: 28 входов, 8 закрытий. Скринер+тренд: 5 целей/2 стопа, net +0.001153USDT, PF1.012. Средний выигрыш0.02012, проигрыш0.04973USDT: безубыток требует71.19% выигрышей, фактически71.43%; преимущества пока нет. Managed: 1 новое закрытие, -0.12791USDT; 11 ещё открыты. Старые FLUID/QNT/ZEC/SKY не относятся к новым входам. Исправлена ошибка предыдущего аудита: partial уже включён в pnl; managed на срезе22:07 -11.0671USDT вместо ошибочных+10.0611. Счета и сделки не переписаны. В all старые позиции занимают99.6% equity при лимите новых входов80%, новых входов там0. Отчёт с формулами и риском: https://vpn.markus.tw1.su/rule-evaluation.html'
  code='''from src.trade.atomic_store import transaction
from src.trade.ledger import Ledger
with transaction(Ledger('/opt/crypto-trade/data/trade/screener-managed')) as tx:
 with tx.connection:tx.connection.execute('INSERT OR IGNORE INTO outbox(id,text) VALUES(?,?)',('rule-evaluation-report-20261006',%r))
print('Report queued')
''' % message
  print(run(s,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -c '+shlex.quote(code),quiet=True))
 finally:s.close()
 cfg={}
 for line in (Path.home()/'.ssh/screener-web-access.txt').read_text('utf-8').splitlines():
  if ':' in line:k,v=line.split(':',1);cfg[k.strip().lower()]=v.strip()
 auth=base64.b64encode((cfg['login']+':'+cfg['password']).encode()).decode();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for path in ['/rule-evaluation.html','/RULE-EVALUATION-20261006.md','/position-review.html']:
  r=opener.open(urllib.request.Request('https://vpn.markus.tw1.su'+path,headers={'Authorization':'Basic '+auth}),timeout=30);body=r.read().decode('utf8');print(path,r.status,len(body));assert 'partial' in body
if __name__=='__main__':main()
