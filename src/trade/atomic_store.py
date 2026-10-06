"""Authoritative SQLite state, journal and notification outbox for paper profiles."""
import json,os,sqlite3,time,uuid
from contextlib import contextmanager,closing
from dataclasses import asdict
from pathlib import Path

class Busy(RuntimeError):pass

def database(root):return Path(root)/'execution.db'

def read_state(root):
 if not database(root).exists():return None
 with closing(sqlite3.connect(database(root),timeout=5)) as c:
  row=c.execute('SELECT payload FROM state WHERE id=1').fetchone()
 return json.loads(row[0]) if row else None

def read_journal(root):
 with closing(sqlite3.connect(database(root),timeout=5)) as c:
  return [json.loads(r[0]) for r in c.execute('SELECT payload FROM journal ORDER BY id')]

def processed(root,paths):
 if not database(root).exists():return set()
 with closing(sqlite3.connect(database(root),timeout=5)) as c:
  return {str(p) for p in paths if c.execute('SELECT 1 FROM consumed WHERE path=?',(str(p),)).fetchone()}

@contextmanager
def profile_lock(root):
 path=Path(root)/'execution.lock';path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a+b') as f:
  if os.name=='nt':
   import msvcrt
   if not f.tell():f.write(b'0');f.flush()
   f.seek(0)
   try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
   except OSError:raise Busy('profile busy')
  else:
   import fcntl
   try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise Busy('profile busy')
  try:yield
  finally:
   if os.name=='nt':f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
   else:fcntl.flock(f,fcntl.LOCK_UN)

def replace_json(path,payload):
 path=Path(path);tmp=path.with_name(path.name+'.projection.tmp')
 with tmp.open('w',encoding='utf-8') as f:f.write(payload);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)

class Transaction:
 def __init__(self,ledger):self.ledger=ledger;self.rows=[];self.halt_reason=None;self.consumed=[];self.inbox_files=[];self.committed=False
 def log(self,kind,**data):
  row=dict(ts=int(time.time()*1000),kind=kind,event_id=uuid.uuid4().hex,**data);self.rows.append(row);return row
 def commit(self,state,events=(),notify=False,commands=()):
  c=self.connection
  with c:
   for row in self.rows:c.execute('INSERT INTO journal(payload) VALUES(?)',(json.dumps(row,ensure_ascii=False),))
   c.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(asdict(state),ensure_ascii=False),))
   for path in list(commands)+self.inbox_files:c.execute('INSERT OR IGNORE INTO consumed VALUES(?)',(str(path),))
   if notify and events:
    text='Бот по скринеру ('+self.ledger.root.name+'):\n'+'\n'.join(events)
    for offset in range(0,len(text),3500):c.execute('INSERT INTO outbox(id,text) VALUES(?,?)',(uuid.uuid4().hex,text[offset:offset+3500]))
  self.committed=True
  # Files are derived views; after a crash the database remains authoritative.
  payload=c.execute('SELECT payload FROM state WHERE id=1').fetchone()[0]
  replace_json(self.ledger.state_path,payload)
  if self.rows or not self.ledger.journal_path.exists():
   replace_json(self.ledger.journal_path,''.join(row+'\n' for row, in c.execute('SELECT payload FROM journal ORDER BY id')))
  if self.halt_reason:self.ledger.halt_path.write_text(self.halt_reason,encoding='utf-8')
  for path in commands:
   try:Path(path).rename(Path(path).with_suffix('.done'))
   except OSError:pass # SQL consumed record prevents replay after a crash.
  for path in self.consumed+self.inbox_files:Path(path).unlink(missing_ok=True)

@contextmanager
def transaction(ledger):
 with profile_lock(ledger.root):
  with closing(sqlite3.connect(database(ledger.root),timeout=5)) as c:
   c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA synchronous=FULL')
   c.executescript('CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS consumed(path TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,text TEXT NOT NULL,sent INTEGER DEFAULT 0,attempts INTEGER DEFAULT 0,next_attempt REAL DEFAULT 0,last_error TEXT); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);')
   if not c.execute("SELECT 1 FROM meta WHERE key='imported'").fetchone():
    with c:
     if ledger.state_path.exists():c.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(ledger.state_path.read_text('utf-8'),))
     if ledger.journal_path.exists():
      for line in ledger.journal_path.read_text('utf-8').splitlines():
       if line:json.loads(line);c.execute('INSERT INTO journal(payload) VALUES(?)',(line,))
     c.execute("INSERT INTO meta VALUES('imported','1')")
   tx=Transaction(ledger);tx.connection=c;ledger._transaction=tx
   try:yield tx
   finally:ledger._transaction=None

def append_journal(root,row):
 with closing(sqlite3.connect(database(root),timeout=5)) as c:
  with c:c.execute('INSERT INTO journal(payload) VALUES(?)',(json.dumps(row,ensure_ascii=False),))
