"""Shared server selection for the Can trade page and its paper bot."""
def select(data):
 rows={r['symbol']:r for r in data.get('rows',[])};best={}
 for f in data.get('forms',[]):
  if not f.get('plan_ok'):continue
  if not f.get('triggered') or (f.get('age') or 0)>1 or f.get('tf') not in ('5m','15m','1h'):continue
  if f.get('kind')=='trendline_bounce' or any(f.get(k) is None for k in ('entry','stop','target')):continue
  if f.get('rr') is not None and f['rr']<1:continue
  r=rows.get(f['symbol'],{});side=f['dir'];trend=(r.get('trend',{}).get('15m' if f['tf']=='5m' else f['tf']) or {}).get('side')
  if side!=trend:continue
  p=r.get('price')
  if p is not None and not (f['stop']<p<f['target'] if side=='long' else f['target']<p<f['stop']):continue
  exp=f.get('exp') or 0;rank=(2 if f.get('sig') and exp>0 else 1 if exp>0 else 0)*100+exp-(f.get('age') or 0)*.01
  key=(f['symbol'],f['tf'],side)
  if key not in best or rank>best[key][0]:best[key]=(rank,f)
 return [f for _,f in sorted(best.values(),key=lambda x:x[0],reverse=True)]


def bot_rows(setups):
 return [dict(symbol=f['symbol'],tf=f['tf'],kind=f['kind'],title=f['title'],direction=f['dir'],entry=f['entry'],stop=f['stop'],target=f['target'],rr=f['rr'],triggered=True,age_candles=f.get('age') or 0,ts=f.get('ts'),key_suffix='page:'+str(f.get('ts') or f['entry']),exp_net=f.get('exp'),measured={'n':f.get('n') or 0},reasons=f.get('reasons') or [],page_setups=True) for f in setups]
