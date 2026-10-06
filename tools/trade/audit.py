"""Read-only reconciliation and operational report for paper profiles."""
from pathlib import Path
import argparse,json,time,html,os
from collections import Counter

def snapshot(d):
 for _ in range(3):
  before=(d/'state.json').read_text(encoding='utf-8')
  journal=[json.loads(l) for l in (d/'journal.jsonl').read_text(encoding='utf-8').splitlines() if l]
  if before==(d/'state.json').read_text(encoding='utf-8'):
   return json.loads(before),journal
 raise RuntimeError('state changed repeatedly during read')

def profile(d):
 st,j=snapshot(d);intraday=d.name.startswith('screener-');cash=st['start_equity'];partial={};entry={};fees=0;funding=0
 for r in j:
  kind=r['kind'];key=r.get('key')
  if intraday:
   if kind=='open':entry[key]=r.get('fee',0);partial[key]=0;cash-=entry[key];fees+=entry[key]
   elif kind=='partial':delta=r['pnl']-r['fee'];cash+=delta;partial[key]=partial.get(key,0)+delta;fees+=r['fee']
   elif kind=='close':
    cash+=r['pnl']-r['fee']-r.get('funding',0)-partial.get(key,0)+entry.get(key,0)
    fees+=r['fee']-entry.get(key,0);funding+=r.get('funding',0)
  else:
   if kind=='fill':cash+=(-1 if r['side']=='buy' else 1)*r['qty']*r['price']-r['fee'];fees+=r['fee']
   elif kind=='funding':cash-=r['total'];funding+=r['total']
 values=[st['start_equity']]+[r['equity'] for r in j if r['kind']=='equity'];peak=values[0];dd=0
 if intraday:
  equity=st['cash']+sum((1 if p['side']=='long' else -1)*p['qty']*((p.get('mark') or p['entry'])-p['entry']) for p in st['positions'].values())
 else:
  latest=next((r for r in reversed(j) if r['kind']=='equity'),{})
  prices=latest.get('prices',{});equity=st['cash']+sum(q*prices.get(s,0) for s,q in st['positions'].items())
 values.append(equity)
 for v in values:peak=max(peak,v);dd=max(dd,1-v/peak)
 closes=[r for r in j if r['kind']=='close'];rs=[r['r_net'] for r in closes]
 return dict(profile=d.name,mode=st.get('mode','paper'),equity=round(equity,6),return_pct=round((equity/st['start_equity']-1)*100,3),cash=st['cash'],replayed_cash=cash,cash_delta=st['cash']-cash,reconciled=abs(st['cash']-cash)<0.0001,positions=len(st['positions']),closed=len(closes),fills=sum(r['kind']=='fill' for r in j),mean_r=sum(rs)/len(rs) if rs else None,max_drawdown_pct=round(dd*100,3),fees=fees,funding=funding,state_age_seconds=round(time.time()-(d/'state.json').stat().st_mtime),valuation_age_seconds=round(time.time()-j[-1]['ts']/1000) if j else None,last_errors=[{'where':r.get('where'), 'symbol':r.get('symbol'),'ts':r['ts']} for r in j if r['kind']=='error'][-5:],halted=(d/'HALT').exists(),paused=(d/'PAUSE').exists())

def write(path,text):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(text,encoding='utf-8');os.replace(tmp,path)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path('data/trade'));ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 profiles=[profile(d) for d in sorted(a.root.iterdir()) if d.is_dir() and (d/'state.json').exists()]
 report={'generated_unix':time.time(),'note':'Paper only. Daily portfolio valuations update at its daily run. Funding uses approximate reference prices; intraday funding is booked on close. Historical statistics are not proof of profitability.','profiles':profiles}
 write(a.out/'paper-audit.json',json.dumps(report,ensure_ascii=False,indent=2))
 rows=''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in [r['profile'],f"{r['equity']:.2f}",f"{r['return_pct']:+.3f}%",r['positions'],r['closed'] or r['fills'],f"{r['fees']:.3f}",f"{r['funding']:.4f}",f"{r['max_drawdown_pct']:.2f}%",f"{r['cash_delta']:.8f}",'OK' if r['reconciled'] else 'CHECK',r['state_age_seconds']])+ '</tr>' for r in profiles)
 write(a.out/'paper-audit.html','<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Аудит бумажных ботов</title><style>body{background:#111827;color:#e5e7eb;font:16px system-ui;margin:24px}a{color:#93c5fd}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #374151;text-align:right}td:first-child{text-align:left}section{overflow:auto}</style><a href="/">Скринер</a> · <a href="/bots.html">Боты</a> · <a href="/research.html">Исследование</a><h1>Бумажные боты: сверка учёта</h1><p>Проверено: '+time.strftime('%Y-%m-%d %H:%M:%S %Z')+'</p><p>Только бумага. Капитал дневных портфелей оценён при последнем дневном запуске. Короткая история не доказывает прибыльность. Funding пока оценивается приближённо; у внутридневных ботов списывается при закрытии.</p><section><table><tr><th>Профиль</th><th>Капитал</th><th>Результат</th><th>Позиции</th><th>Закрытия / заявки</th><th>Комиссии</th><th>Funding</th><th>Макс. просадка</th><th>Расхождение cash</th><th>Сверка</th><th>Возраст состояния, с</th></tr>'+rows+'</table></section><p>Исторические журналы сохранены. Оценка капитала дневных портфелей использует округлённые цены из журнала.</p></html>')
 print(json.dumps([{'profile':r['profile'],'equity':r['equity'],'reconciled':r['reconciled'],'cash_delta':r['cash_delta']} for r in profiles],indent=2))
 if not all(r['reconciled'] and r['mode']=='paper' for r in profiles):raise SystemExit(1)
if __name__=='__main__':main()
