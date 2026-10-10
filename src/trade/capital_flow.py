"""Paper deposits and unitised returns; contributed money is not trading PnL."""
import math,time
from src.trade.atomic_store import transaction
from src.trade.intraday import load_state
def performance(initial,rows,equity):
 units=initial;curve=[1.0];deposits=0.0
 for r in rows:
  if r['kind']=='equity' and units>0:curve.append(r['equity']/units)
  elif r['kind']=='capital_flow':
   before=r['equity_before'];value=before/units
   if value<=0:raise ValueError('cannot unitise an insolvent account')
   curve.append(value);units+=r['amount']/value;deposits+=r['amount']
 curve.append(equity/units if units else 0)
 peak=curve[0];dd=0
 for value in curve:
  peak=max(peak,value)
  if peak>0:dd=min(dd,value/peak-1)
 return dict(deposits=deposits,funded_capital=initial+deposits,pnl=equity-initial-deposits,return_fraction=curve[-1]-1,drawdown_fraction=dd,curve=[v*initial for v in curve])
def top_up(ledger,target,operation_id,now_ms=None):
 if not math.isfinite(target) or target<=0:raise ValueError('positive finite target required')
 if not ledger.root.name.startswith('screener-'):raise ValueError('paper screener profiles only')
 now_ms=now_ms if now_ms is not None else int(time.time()*1000)
 with transaction(ledger) as tx:
  if tx.connection.execute('SELECT 1 FROM meta WHERE key=?',('capital-flow:'+operation_id,)).fetchone():return None
  st=load_state(ledger.state_path,1000,now_ms);rows=ledger.journal();funded=st.start_equity+sum(r['amount'] for r in rows if r['kind']=='capital_flow')
  amount=max(0,target-funded)
  if not amount:return None
  before=st.equity();cash_before=st.cash;peak_before=st.peak
  st.cash+=amount;st.peak+=amount # scale current monetary risk base, preserve loss in dollars
  ledger.log('capital_flow',operation_id=operation_id,amount=amount,currency='USDT',mode='paper',reason='увеличение виртуального капитала для проверки сетапов',equity_before=before,equity_after=st.equity(),cash_before=cash_before,cash_after=st.cash,peak_before=peak_before,peak_after=st.peak,funded_before=funded,funded_after=funded+amount)
  ledger.log('equity',equity=st.equity(),cash=st.cash,open=len(st.positions),capital_flow_id=operation_id)
  tx.commit(st,[f'Виртуальное пополнение +{amount:.2f} USDT. Внесённый капитал {funded+amount:.2f} USDT. Это не торговая прибыль; история и позиции сохранены.'],notify=True)
  with tx.connection:tx.connection.execute('INSERT INTO meta(key,value) VALUES(?,?)',('capital-flow:'+operation_id,str(amount)))
  return dict(amount=amount,before=before,after=st.equity(),funded=funded+amount)
