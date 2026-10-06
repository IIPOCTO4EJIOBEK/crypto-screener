"""At-least-once Telegram delivery, with durable retries and masked errors."""
import os,sqlite3,time
from contextlib import closing
from src.trade.atomic_store import database

def drain_one(root,send=None,now=None):
 now=time.time() if now is None else now
 if not database(root).exists():return False
 if send is None:
  token,chat=os.environ.get('TELEGRAM_BOT_TOKEN'),os.environ.get('TELEGRAM_CHAT_ID')
  if not token or not chat:return False
  def send(text):
   import requests
   response=requests.post('https://api.telegram.org/bot'+token+'/sendMessage',json=dict(chat_id=chat,text=text),timeout=10)
   data=response.json()
   return bool(response.ok and data.get('ok')),str(response.status_code),float(data.get('parameters',{}).get('retry_after',0))
 with closing(sqlite3.connect(database(root),timeout=5)) as c:
  c.execute('BEGIN IMMEDIATE')
  row=c.execute('SELECT id,text,attempts FROM outbox WHERE sent=0 AND next_attempt<=? ORDER BY rowid LIMIT 1',(now,)).fetchone()
  if not row:c.rollback();return False
  ident,text,attempts=row;c.execute('UPDATE outbox SET next_attempt=?,attempts=attempts+1 WHERE id=?',(now+60,ident));c.commit()
  try:ok,error,retry=send(text)
  except Exception as exc:ok,error,retry=False,type(exc).__name__,0
  with c:
   if ok:c.execute('UPDATE outbox SET sent=1,last_error=NULL WHERE id=?',(ident,))
   else:c.execute('UPDATE outbox SET next_attempt=?,last_error=? WHERE id=?',(now+max(retry,min(300,2**min(attempts+1,8))),str(error)[:80],ident))
 return True
