"""Queue the authorised audit report and exact committed close-event photos."""
from pathlib import Path
import json,shlex
from deploy_server import connect,run,HOST
ROOT=Path(__file__).resolve().parents[1]
MESSAGE='Проверка стопов завершена. Срез до изменения: 137 позиций, 247 закрытий, 120 стопов. Найдены входы только по всплеску объёма, дальние стопы и потеря подтверждённой отмены идеи при исчезновении свежего сигнала. Исправлено: объём — кандидат; пробой/ретест проверяются по точному уровню, закрытию и текущему исполнению; новые авто-входы со стопом >3% на 5м, >5% на 15м, >8% на 1ч пропускаются; при доступной истории предел 3 доимпульсных ATR. Добавлено постоянное сопровождение отмены идеи. По нему закрылись бумажные FLUID, QNT, ZEC и SKY; стоп/тейк приоритетны. Старые стопы и история не переписаны. Проверки VPS: 122 торговли + 8 потоков; health success. Меньше стопов и прибыльность новых правил пока не доказаны — оцениваем отдельно новые сделки после расходов. Все позиции и группы истории: https://vpn.markus.tw1.su/position-review.html'
def main():
 data=json.loads((ROOT/'deployment/stop-review-after.json').read_text('utf-8'))
 events=[e for e in data['events'] if e.get('reason')=='invalidation']
 code='''import json,time,pathlib,sqlite3
from src.trade.atomic_store import transaction,Busy
from src.trade.intraday import load_state
from src.trade.ledger import Ledger
from src.trade.photo_reports import event_media
root=pathlib.Path('/opt/crypto-trade/data/trade');events=%r;message=%r;queued=[]
for e in events:
 folder=root/e['profile'];ledger=Ledger(folder)
 for attempt in range(5):
  try:
   with transaction(ledger) as tx:
    with tx.connection:
     found=False
     for raw, in tx.connection.execute('select media from outbox where media is not null'):
      m=json.loads(raw)
      if m.get('event')=='close' and m.get('when')==e['ts'] and m.get('position',{}).get('symbol')==e['symbol']:found=True;break
     if not found:
      state=load_state(ledger.state_path,1000,int(time.time()*1000));media=event_media(e['profile'],state,[e])[0]
      ident='stop-review-photo-'+e['profile']+'-'+str(e['ts']);tx.connection.execute('INSERT OR IGNORE INTO outbox(id,text,media) VALUES(?,?,?)',(ident,media['caption'],json.dumps(media,ensure_ascii=False)))
      queued.append((e['profile'],e['symbol']))
   break
  except Busy:time.sleep(1)
 else:raise RuntimeError('photo profile busy')
ledger=Ledger(root/'screener-managed')
with transaction(ledger) as tx:
 with tx.connection:tx.connection.execute('INSERT OR IGNORE INTO outbox(id,text) VALUES(?,?)',('stop-review-report-20261006',message))
print('Photos queued',queued,'report queued')
''' % (events,MESSAGE)
 s=connect(HOST,'root')
 try:
  print(run(s,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -c '+shlex.quote(code),quiet=True))
 finally:s.close()
if __name__=='__main__':main()
