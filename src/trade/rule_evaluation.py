"""Cohort evaluation: close.pnl already includes partial realised results."""
from collections import Counter,defaultdict
import math,statistics
START=1791313657000
def net(row):return row['pnl']-row.get('fee',0)-(row.get('funding') or 0)
def new_rules(row):return 'prior_atr' in (row.get('rules') or {}) and row.get('opened_ms',row.get('ts',0))>=START
def usable(row):return row.get('qty',0)*row.get('entry',0)>1e-6
def wilson(k,n):
 if not n:return None
 z=1.95996398454;p=k/n;den=1+z*z/n
 center=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
 return [(center-half)*100,(center+half)*100]
def metrics(rows):
 settled=[r for r in rows if not r.get('funding_pending')];ns=[net(r) for r in settled];wins=[v for v in ns if v>1e-8];loss=[-v for v in ns if v<-1e-8]
 gain=sum(wins);lost=sum(loss);balance=peak=dd=0
 for r in sorted(settled,key=lambda r:r['exit_ts']):
  balance+=net(r);peak=max(peak,balance);dd=max(dd,peak-balance)
 avgwin=statistics.mean(wins) if wins else None;avgloss=statistics.mean(loss) if loss else None
 rs=[net(r)/r['funding_risk_quote'] for r in settled if r.get('funding_risk_quote',0)>0]
 return dict(closed=len(rows),settled=len(settled),pending=len(rows)-len(settled),net=sum(ns),net_provisional=sum(net(r) for r in rows),fees=sum(r.get('fee',0) for r in settled),funding=sum(r.get('funding') or 0 for r in settled),reasons=dict(Counter(r['reason'] for r in rows)),wins=len(wins),win_rate=100*len(wins)/len(ns) if ns else None,win_rate_ci95=wilson(len(wins),len(ns)),profit_factor=gain/lost if lost else None,avg_win=avgwin,avg_loss=avgloss,break_even_win_rate=100*avgloss/(avgwin+avgloss) if avgwin and avgloss else None,avg_r=statistics.mean(rs) if rs else None,r_n=len(rs),closed_curve_drawdown_usdt=dd)
def evaluate(data):
 out={'updated_ms':data['updated_ms'],'profiles':{},'groups':[],'legacy_exits_after_install':[]}
 for name,p in sorted(data['profiles'].items()):
  rows=p['rows'];closed=[r for r in rows if r['kind']=='close' and usable(r)];old=[r for r in closed if not new_rules(r)];new=[r for r in closed if new_rules(r)];opens=[r for r in rows if r['kind']=='open' and new_rules(r) and usable(r)]
  st=p['state'];positions=[r for r in st['positions'].values() if r['qty']*r['entry']>1e-6]
  live=[r for r in positions if new_rules(dict(r,rules=r.get('entry_rules')))];unreal=sum((1 if r['side']=='long' else -1)*r['qty']*((r.get('mark') or r['entry'])-r['entry']) for r in positions)
  eq=st['cash']+unreal;marknet=sum((1 if r['side']=='long' else -1)*r['qty']*((r.get('mark') or r['entry'])-r['entry'])+r.get('realized',0)-r['fee_in'] for r in live)
  exit_fees=sum(r['qty']*(r.get('mark') or r['entry'])*r['fee_in']/((r.get('qty0') or r['qty'])*r['entry']) for r in live)
  notional=sum(r['qty']*(r.get('mark') or r['entry']) for r in positions)
  stop_loss=sum(max(0,(1 if r['side']=='long' else -1)*((r.get('mark') or r['entry'])-r['stop']))*r['qty'] for r in positions)
  skips=[r for r in rows if r['kind']=='skip' and r['ts']>=START];skiptypes=Counter(r.get('reason','?') for r in skips)
  out['profiles'][name]={'old':metrics(old),'new':metrics(new),'new_opened':len(opens),'new_live':len(live),'new_open_mark_net_before_exit_costs_funding':marknet,'new_estimated_exit_fees':exit_fees,'new_open_mark_after_estimated_exit_fees_before_funding':marknet-exit_fees,'current_equity':eq,'peak_drawdown_pct':max(0,100*(st['peak']-eq)/st['peak']) if st['peak'] else None,'all_position_notional':notional,'capital_used_pct':100*notional/eq if eq else None,'loss_from_mark_at_current_stops_before_costs':stop_loss,'stop_risk_pct':100*stop_loss/eq if eq else None,'total_open':len(positions),'skip_retries':dict(skiptypes),'new_unique_signals':len({(r['symbol'],r['tf'],r.get('formation'),r.get('side'),r.get('signal_ts')) for r in opens})}
  grouped=defaultdict(list)
  for r in new:grouped[(r.get('formation'),r['tf'],r['side'],r.get('trend'))].append(r)
  for (kind,tf,side,trend),items in grouped.items():out['groups'].append(dict(profile=name,kind=kind,tf=tf,side=side,trend=trend,**metrics(items)))
  out['legacy_exits_after_install'] += [dict(profile=name,symbol=r['symbol'],reason=r['reason'],net=net(r),pending=bool(r.get('funding_pending'))) for r in old if r['ts']>=START and r['reason']=='invalidation']
 return out
