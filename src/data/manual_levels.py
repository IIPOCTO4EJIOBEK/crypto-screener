"""Persistent chart drawings and one-shot price crossings, with a Telegram outbox."""
import json,sqlite3,math,re,time,uuid
from pathlib import Path
from contextlib import closing

class Store:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  with closing(sqlite3.connect(self.path,timeout=10)) as c:
   c.executescript('CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,payload TEXT NOT NULL,active INTEGER,last_price REAL); CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT NOT NULL,ack INTEGER DEFAULT 0,delivered INTEGER DEFAULT 0);');c.commit()
 def snapshot(self):
  with closing(sqlite3.connect(self.path,timeout=10)) as c:
   items=[dict(json.loads(p),active=bool(a)) for p,a in c.execute('SELECT payload,active FROM items')]
   events=[dict(json.loads(p),ack=bool(a),delivered=bool(d)) for p,a,d in c.execute('SELECT payload,ack,delivered FROM events ORDER BY rowid DESC LIMIT 100')]
  return dict(items=items,events=events)
 def save(self,item):
  symbol=str(item.get('symbol','')).upper();kind=item.get('kind','level');tf=item.get('tf','15m')
  if not re.fullmatch(r'\w{1,35}USDT',symbol) or tf not in ('5m','15m','1h') or kind not in ('level','horizontal','trend'):raise ValueError('invalid symbol, kind or timeframe')
  ident=str(item.get('id') or uuid.uuid4().hex)
  if not re.fullmatch(r'[a-f0-9]{32}',ident):raise ValueError('invalid id')
  def number(v):
   x=float(v)
   if not math.isfinite(x) or x<=0 or x>1e12:raise ValueError('invalid coordinate')
   return x
  row=dict(id=ident,symbol=symbol,kind=kind,tf=tf,label=str(item.get('label',''))[:80],updated_ms=int(time.time()*1000))
  if kind=='trend':
   points=item.get('points',[])
   if len(points)!=2:raise ValueError('two points required')
   row['points']=sorted([[number(p[0]),number(p[1])] for p in points])
   if row['points'][0][0]==row['points'][1][0]:raise ValueError('times must differ')
  else:row['price']=number(item.get('price'))
  row['direction']=item.get('direction','either')
  if row['direction'] not in ('up','down','either'):raise ValueError('invalid direction')
  with closing(sqlite3.connect(self.path,timeout=10)) as c:
   c.execute('BEGIN IMMEDIATE')
   if c.execute('SELECT count(*) FROM items').fetchone()[0]>=100 and not c.execute('SELECT 1 FROM items WHERE id=?',(ident,)).fetchone():raise ValueError('limit 100 drawings')
   previous=c.execute('SELECT active FROM items WHERE id=?',(ident,)).fetchone()
   active=int(kind=='level' and (previous[0] if previous else True))
   c.execute('INSERT OR REPLACE INTO items VALUES(?,?,?,NULL)',(ident,json.dumps(row,ensure_ascii=False),active));c.commit()
  return row
 def change(self,op,ident):
  with closing(sqlite3.connect(self.path,timeout=10)) as c:
   if op=='delete':c.execute('DELETE FROM items WHERE id=?',(ident,))
   elif op=='rearm':c.execute('UPDATE items SET active=1,last_price=NULL WHERE id=? AND json_extract(payload,"$.kind")="level"',(ident,))
   elif op=='ack':c.execute('UPDATE events SET ack=1 WHERE id=?',(ident,))
   elif op=='delivered':c.execute('UPDATE events SET delivered=1 WHERE id=?',(ident,))
   else:raise ValueError('unknown operation')
   c.commit()
 def observe(self,prices,now_ms):
  events=[]
  with closing(sqlite3.connect(self.path,timeout=10)) as c:
   c.execute('BEGIN IMMEDIATE')
   for ident,payload,previous in c.execute('SELECT id,payload,last_price FROM items WHERE active=1').fetchall():
    x=json.loads(payload);current=prices.get(x['symbol'])
    if current is None or not math.isfinite(current) or current<=0:continue
    level=x.get('price');direction=x['direction']
    up=previous is not None and previous<level<=current
    down=previous is not None and previous>level>=current
    hit=(up and direction in ('up','either')) or (down and direction in ('down','either'))
    if hit:
     event=dict(id=uuid.uuid4().hex,level_id=ident,symbol=x['symbol'],tf=x['tf'],price=level,observed_price=current,direction='up' if up else 'down',label=x['label'],ts=now_ms)
     c.execute('INSERT INTO events(id,payload) VALUES(?,?)',(event['id'],json.dumps(event,ensure_ascii=False)))
     c.execute('UPDATE items SET active=0,last_price=? WHERE id=?',(current,ident));events.append(event)
    else:c.execute('UPDATE items SET last_price=? WHERE id=?',(current,ident))
   c.execute('DELETE FROM events WHERE rowid NOT IN (SELECT rowid FROM events ORDER BY rowid DESC LIMIT 1000) AND delivered=1 AND ack=1');c.commit()
  return events
