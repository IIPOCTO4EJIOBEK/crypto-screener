
import json
from pathlib import Path
from src.trade.intraday import load_state,save_state,msk_day
root=Path('data/trade')
for d in root.glob('screener-*'):
 if not d.is_dir() or not (d/'state.json').exists():continue
 st=load_state(d/'state.json',1000,0)
 journal=[json.loads(l) for l in (d/'journal.jsonl').read_text().splitlines() if l]
 count=0
 for key,p in st.positions.items():
  if p.get('tp1_done') and not p.get('funding_legs'):
   legs=[{'qty':r['qty'],'end_ms':r['ts']//60000*60000} for r in journal if r['kind']=='partial' and r.get('key')==key and r['ts']>=p['opened_ms']]
   p['funding_legs']=legs;count+=len(legs)
 # Correct current-day fees to receipt-time accounting without changing cash/history.
 partial={};entry={};day_pnl=0
 for r in journal:
  key=r.get('key');kind=r['kind'];delta=0
  if kind=='open':entry[key]=r.get('fee',0);partial[key]=0;delta=-entry[key]
  elif kind=='partial':delta=r['pnl']-r['fee'];partial[key]=partial.get(key,0)+delta
  elif kind=='close':delta=r['pnl']-r['fee']-r.get('funding',0)-partial.get(key,0)+entry.get(key,0)
  if msk_day(r['ts'])==st.day:day_pnl+=delta
 old=st.day_pnl;st.day_pnl=day_pnl
 save_state(d/'state.json',st)
 print(d.name,'restored funding legs',count,'day pnl',round(old,4),'->',round(day_pnl,4))
