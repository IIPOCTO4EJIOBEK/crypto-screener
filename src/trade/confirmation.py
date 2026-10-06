"""Do not execute a retest after its confirmation has been lost."""
import json,time
from pathlib import Path
from src.data.market import INTERVALS

def annotate(rows,root,now=None):
 now=int(time.time()*1000) if now is None else now
 for r in rows:
  duration=INTERVALS.get(r.get('tf'),300)*1000
  if r.get('ts'):r['age_candles']=max(0,int((now-(r.get('ready_ms') or r['ts']+duration))//duration))
  structural=r.get('kind') in ('retest','breakout','structure_break')
  r['_entry_safety']=True
  if structural:r['_requires_confirmation']=True
  r.pop('_risk_atr',None)
  r.pop('_latest_closed',None);r.pop('_latest_closed_ts',None)
  try:
   cs=json.loads((Path(root)/(r['symbol']+'_'+r['tf']+'.json')).read_text('utf-8'))
   closed=[c for c in cs if c[0]+duration<=now]
   from src.data.entry_safety import prior_atr
   r['_risk_atr']=prior_atr(closed,r.get('ts') or now)
   last=closed[-1]
   if last[0]+duration!=now//duration*duration:continue
   r['_latest_closed']=float(last[4]);r['_latest_closed_ts']=int(last[0])
  except (OSError,ValueError,KeyError,IndexError):pass
 return rows

def entry_confirmation(row,price):
 if not row.get('manual'):
  if row.get('kind')=='volume_splash':return 'всплеск объёма — кандидат; нужен отдельный пробой или ретест уровня'
  if row.get('_entry_safety') and 'stop' in row:
   from src.data.entry_safety import stop_guard
   why=stop_guard(price,row['stop'],row['tf'],row.get('_risk_atr'))
   if why:return why
 if row.get('kind') not in ('retest','breakout','structure_break'):return None
 level=row.get('trigger_level') or 0
 if not level:
  return 'нет точного уровня подтверждения '+row['kind']+'; ждём обновлённый сигнал' if row.get('_requires_confirmation') else None
 sign=1 if row['direction']=='long' else -1
 if sign*(price-level)<=0:return 'сигнал больше не подтверждён: текущая цена за уровнем с обратной стороны'
 closed=row.get('_latest_closed')
 if closed is None and row.get('_requires_confirmation'):return 'нет свежей закрытой свечи для подтверждения сигнала'
 if closed is not None and sign*(closed-level)<=0:return 'сигнал отменён последней закрытой свечой за уровнем с обратной стороны'
 return None
