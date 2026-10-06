"""Fetch entry order books before taking the profile writer lock."""
import time
from src.trade.broker import PaperBroker,fetch_book
from src.trade.intraday import signal_key

class EntryBroker(PaperBroker):
 def __init__(self,market='future',books=None,clock=time.time):
  self.books=books or {};self.clock=clock
  super().__init__(market,book=self.cached_book)
 def cached_book(self,symbol,market):
  item=self.books.get(symbol)
  if not item or not 0<=self.clock()-item[0]<=5:
   raise ValueError('fresh entry order book unavailable; retry next pass')
  return item[1]
 def execute(self,symbol,side,qty,reduce=False):
  if reduce:
   return PaperBroker(self.market).execute(symbol,side,qty,reduce=True)
  try:return super().execute(symbol,side,qty,reduce=False)
  except ValueError:return None

def prepare(rows,state,cfg,books=None,fetch=fetch_book,clock=time.time):
 books=books if books is not None else {}
 slots=max(0,cfg.max_open-len(state.positions))
 if not slots or state.equity()*cfg.capital_fraction<=sum(p.qty*(p.mark or p.entry) for p in state.pos()):return EntryBroker(books=books,clock=clock)
 symbols=[]
 for r in rows:
  if not r.get('triggered') or (r.get('age_candles') or 0)>cfg.max_age or signal_key(r) in state.seen:continue
  if any(p.symbol==r['symbol'] and (not cfg.parallel_timeframes or p.tf==r['tf']) for p in state.pos()):continue
  if r['symbol'] not in symbols:symbols.append(r['symbol'])
  if len(symbols)>=slots:break
 for symbol in symbols:
  saved=books.get(symbol)
  if saved and 0<=clock()-saved[0]<=5:continue
  try:
   book=fetch(symbol,'future');books[symbol]=(clock(),book)
  except Exception:books.pop(symbol,None)
 return EntryBroker(books=books,clock=clock)
