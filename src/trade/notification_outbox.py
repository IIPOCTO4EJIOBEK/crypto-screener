"""At-least-once Telegram delivery, with durable retries and masked errors."""
import os,sqlite3,time,json
from contextlib import closing
from src.trade.atomic_store import database

def drain_one(root,send=None,now=None,send_photo=None):
 now=time.time() if now is None else now
 if not database(root).exists():return False
 if send is None:
  token,chat=os.environ.get('TELEGRAM_BOT_TOKEN'),os.environ.get('TELEGRAM_CHAT_ID')
  if not token or not chat:return False
  import requests
  session=requests.Session();session.trust_env=False
  def result(response):
   data=response.json()
   return bool(response.ok and data.get('ok')),str(response.status_code),float(data.get('parameters',{}).get('retry_after',0))
  def send(text):
   return result(session.post('https://api.telegram.org/bot'+token+'/sendMessage',json=dict(chat_id=chat,text=text),timeout=10))
  def send_photo(text,path):
   with open(path,'rb') as image:
    return result(session.post('https://api.telegram.org/bot'+token+'/sendPhoto',data=dict(chat_id=chat,caption=text[:1000]),files={'photo':image},timeout=20))
 with closing(sqlite3.connect(database(root),timeout=5)) as c:
  c.execute('BEGIN IMMEDIATE')
  media_column='media' if 'media' in {r[1] for r in c.execute('PRAGMA table_info(outbox)')} else 'NULL'
  row=c.execute('SELECT id,text,attempts,'+media_column+' FROM outbox WHERE sent=0 AND next_attempt<=? ORDER BY rowid LIMIT 1',(now,)).fetchone()
  if not row:c.rollback();return False
  ident,text,attempts,media=row;c.execute('UPDATE outbox SET next_attempt=?,attempts=attempts+1 WHERE id=?',(now+60,ident));c.commit()
  try:
   if media:
    from pathlib import Path
    from src.trade.photo_reports import chart
    path=Path(root)/'reports'/(ident+'.png')
    if not path.exists():chart(json.loads(media),path)
    if send_photo is None:raise RuntimeError('photo sender unavailable')
    ok,error,retry=send_photo(text,path)
   else:ok,error,retry=send(text)
  except Exception as exc:ok,error,retry=False,type(exc).__name__,0
  with c:
   if ok:c.execute('UPDATE outbox SET sent=1,last_error=NULL WHERE id=?',(ident,))
   else:c.execute('UPDATE outbox SET next_attempt=?,last_error=? WHERE id=?',(now+max(retry,min(300,2**min(attempts+1,8))),str(error)[:80],ident))
 return True

