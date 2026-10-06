import json,time
from src.trade.atomic_store import transaction
from src.trade.intraday import Config,cycle,save_state
from src.trade.entry_snapshot import prepare
from src.trade.position_controls import enqueue,pending,apply
from src.trade.ledger import Ledger
from src.trade import inbox
from tests.test_fast_monitor import initial,position
from tests.test_book_cache import payload

def test_edit_without_book_retains_command_and_old_levels(tmp_path):
 state=initial();state.put(position());ledger=Ledger(tmp_path)
 enqueue(tmp_path,dict(op='edit',key='p',reason='new level',stop=97,target=105))
 broker=prepare([],state,Config(),book_root=tmp_path/'books');consumed=[]
 events=apply(pending(tmp_path),state,broker,ledger,100000,consumed)
 assert not consumed and not events and state.pos()[0].stop==98 and pending(tmp_path)

def test_manual_close_uses_ws_without_rest_and_keeps_reason(tmp_path):
 state=initial();p=position();p.pending_exit_reason='manual';p.manual_reason='слом идеи';p.expires_ms=0;state.put(p)
 root=tmp_path/'books';root.mkdir();(root/'AAAUSDT.json').write_text(json.dumps(payload()))
 broker=prepare([],state,Config(),book_root=root,clock=lambda:100)
 result=cycle([],state,broker=broker,ledger=Ledger(tmp_path),candles=lambda *a:[],now_ms=100000,cfg=Config(no_timeout=True))
 assert result['closed']==1 and not state.positions
 closed=Ledger(tmp_path).journal()[-1]
 assert closed['kind']=='close' and closed['exit']==99 and closed['manual_reason']=='слом идеи'

def test_inbox_missing_book_retries_and_cannot_be_consumed(tmp_path):
 ledger=Ledger(tmp_path);state=initial();path=inbox.put(tmp_path,dict(symbol='AAAUSDT',side='long',stop=98,target=104))
 with transaction(ledger) as tx:
  def missing(*a):raise ValueError('offline')
  rows,bad=inbox.take(tmp_path,missing,transaction=tx,retry_market=True)
  assert not rows and bad and not tx.inbox_files
  tx.commit(state)
 assert path.exists()

def test_inbox_insufficient_depth_survives_then_executes_once(tmp_path,monkeypatch):
 from tools.trade import screener_bot,screener_page
 books=tmp_path/'books';books.mkdir();d=payload();d.update(E=int(time.time()*1000),received=time.time(),a=[['101','.001']]);book=books/'AAAUSDT.json';book.write_text(json.dumps(d))
 monkeypatch.setenv('SCREENER_BOOK_DIR',str(books));monkeypatch.setattr(screener_bot,'screener_rows',lambda *a:([],[]));monkeypatch.setattr(screener_page,'write',lambda *a,**k:None)
 path=inbox.put(tmp_path,dict(symbol='AAAUSDT',side='long',stop=98,target=104))
 args=['--data',str(tmp_path),'--trend','off','--no-timeout']
 assert screener_bot.run_one(args)==0 and path.exists()
 d.update(E=int(time.time()*1000),received=time.time(),a=[['101','1000']]);book.write_text(json.dumps(d))
 assert screener_bot.run_one(args)==0 and not path.exists()
 from src.trade.intraday import load_state
 assert len(load_state(tmp_path/'state.json',0,0).positions)==1
