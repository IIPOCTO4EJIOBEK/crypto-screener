"""Do not execute a retest after its confirmation has been lost."""
import json,time
from pathlib import Path
from src.data.market import INTERVALS

def annotate(rows,root,now=None):
 now=int(time.time()*1000) if now is None else now
 for r in rows:
  duration=INTERVALS.get(r.get('tf'),300)*1000
  if r.get('ts'):r['age_candles']=max(0,int((now-(r.get('ready_ms') or r['ts']+duration))//duration))
  if r.get('kind')!='retest':continue
  r['_requires_confirmation']=True
  r.pop('_latest_closed',None);r.pop('_latest_closed_ts',None)
  try:
   cs=json.loads((Path(root)/(r['symbol']+'_'+r['tf']+'.json')).read_text('utf-8'))
   closed=[c for c in cs if c[0]+duration<=now]
   last=closed[-1]
   if last[0]+duration!=now//duration*duration:continue
   r['_latest_closed']=float(last[4]);r['_latest_closed_ts']=int(last[0])
  except (OSError,ValueError,KeyError,IndexError):pass
 return rows

def entry_confirmation(row,price):
 if row.get('kind')!='retest':return None
 level=row.get('trigger_level') or 0
 if not level:
  return 'нет точного уровня подтверждения ретеста; ждём обновлённый сигнал' if row.get('_requires_confirmation') else None
 sign=1 if row['direction']=='long' else -1
 if sign*(price-level)<=0:return 'ретест больше не подтверждён: текущая цена за уровнем с обратной стороны'
 closed=row.get('_latest_closed')
 if closed is None and row.get('_requires_confirmation'):return 'нет свежей закрытой свечи для подтверждения ретеста'
 if closed is not None and sign*(closed-level)<=0:return 'ретест отменён последней закрытой свечой за уровнем с обратной стороны'
 return None
