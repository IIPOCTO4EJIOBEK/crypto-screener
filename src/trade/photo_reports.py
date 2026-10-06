"""Telegram charts from committed paper positions and recorded local candles."""
import json,os,textwrap
from pathlib import Path

LABELS={'open':'Вход','close':'Выход','partial':'Частичный тейк','control':'Изменение позиции'}

def event_media(profile,state,rows):
 out=[]
 for row in rows:
  event=row.get('kind')
  if event not in LABELS:continue
  p=state.positions.get(row.get('key'))
  if not p:
   if event!='close':continue
   p=dict(row,stop=row.get('stop_final'),target=row.get('target_final'),mark=row.get('exit'),no_target=row.get('no_target',False))
  else:p=dict(p)
  if not p.get('symbol') or not p.get('entry'):continue
  p['no_target']=p.get('no_target',False) or not p.get('target')
  p['equity_at_snapshot']=state.equity()
  p['event_reason']=row.get('manual_reason') or row.get('reason') or ''
  p['closed_net']=row.get('pnl',0)-row.get('fee',0)-row.get('funding',0) if event=='close' else None
  p['funding_pending']=bool(row.get('funding_pending'))
  goal='ведение трейлингом' if p.get('no_target') else f"{p.get('target',0):.7g}"
  caption=f"{LABELS[event]} · {profile} · {p['symbol']} · {p.get('tf','')} · {p.get('side','')}\nВход {p['entry']:.7g} · стоп {p.get('stop',0):.7g} · цель {goal}\nБумажная позиция"
  media=dict(kind='position',profile=profile,position=p,event=event,when=row['ts'],caption=caption[:1000])
  media['candles']=capture(p['symbol'],p.get('tf','5m'),row['ts'])
  out.append(media)
 return out

def capture(symbol,tf,when,market_root=None):
 root=Path(market_root or os.environ.get('SCREENER_CHART_ROOT','/opt/crypto-screener/docs/live/kl'))
 try:
  rows=json.loads((root/(symbol+'_'+tf+'.json')).read_text('utf-8'))
  try:
   live=json.loads((root/(symbol+'.live.json')).read_text('utf-8'));bar=live['k'].get(tf)
   if bar and live['t']<=when and when-live['t']<=10000:
    rows=[r for r in rows if r[0]<bar[0]]+[bar]
  except (OSError,ValueError,KeyError,TypeError):pass
  return [r for r in rows if int(r[0])<=when][-90:]
 except (OSError,ValueError):return []

def chart(media,destination,market_root=None):
 if media.get('kind')=='summary':return summary(media,destination)
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.patches import Rectangle
 from datetime import datetime,timezone,timedelta
 p=media['position'];tf=p.get('tf','5m');symbol=p['symbol']
 rows=media.get('candles')
 if rows is None:rows=capture(symbol,tf,media['when'],market_root)
 plt.style.use('dark_background');fig,ax=plt.subplots(figsize=(14,8),dpi=120)
 fig.patch.set_facecolor('#101827');ax.set_facecolor('#101827');ax.grid(alpha=.16)
 levels=[('Вход',p['entry'],'#49b8ff'),('Стоп',p.get('stop'),'#ff6476')]
 if not p.get('no_target') and p.get('target'):levels.append(('Цель',p.get('target'),'#39d7a0'))
 if p.get('mark'):levels.append(('Текущая / выход',p['mark'],'#f4c56c'))
 if rows:
  for i,r in enumerate(rows):
   o,h,l,c=map(float,r[1:5]);color='#39d7a0' if c>=o else '#ff6476'
   ax.vlines(i,l,h,color=color,linewidth=1)
   ax.add_patch(Rectangle((i-.32,min(o,c)),.64,max(abs(c-o),c*.00001),facecolor=color,edgecolor=color))
  ax.set_xlim(-1,len(rows)+18)
  indices=list(range(0,len(rows),max(1,len(rows)//7)))
  ax.set_xticks(indices,[datetime.fromtimestamp(rows[i][0]/1000,timezone(timedelta(hours=3))).strftime('%d.%m %H:%M') for i in indices],fontsize=9)
 else:
  ax.text(.5,.5,'Нет сохранённых свечей этого таймфрейма\nПоказаны только уровни записанной позиции',ha='center',transform=ax.transAxes)
 for label,value,color in levels:
  if value and value>0:ax.axhline(value,color=color,linestyle='--',linewidth=1.3,label=f'{label}: {value:.7g}')
 if p.get('opened_ms') and rows:
  opened=p['opened_ms'];index=next((i for i,r in enumerate(rows) if r[0]>=opened),None)
  if index is not None:ax.axvline(index,color='#49b8ff',alpha=.4)
 ax.legend(loc='upper left',fontsize=10,framealpha=.9)
 label=LABELS.get(media.get('event'),'Открытая позиция')
 ax.set_title(f"{label} · {symbol} · {tf} · {p.get('side','').upper()} · {media['profile']}",loc='left',fontsize=16,pad=16)
 price=p.get('mark') or p['entry'];qty=p.get('qty',0);side=1 if p.get('side')=='long' else -1
 pnl=p.get('closed_net')
 if pnl is None:pnl=p.get('realized',0)+side*(price-p['entry'])*qty-p.get('fee_in',0)-price*qty*.0005
 reasons=p.get('reasons') or [p.get('title') or p.get('formation') or 'Причина не сохранена в старой позиции']
 if isinstance(reasons,dict):reasons=[str(reasons)]
 if isinstance(reasons,str):reasons=[reasons]
 why='; '.join(map(str,reasons))
 from src.trade.position_metrics import metrics
 pm=metrics(p,p.get('equity_at_snapshot',0));pct=100*pnl/((p.get('qty0') or qty)*p['entry']) if qty else 0
 share=f"{pm['exposure_pct']:.2f}%" if pm['exposure_pct'] is not None else '—'
 if media.get('event')=='close':share='0.00% (закрыта)'
 costs='PnL предварительный: funding ожидает расчёта.' if p.get('funding_pending') else 'Итог после комиссий и funding.' if p.get('closed_net') is not None else 'Комиссия выхода оценочная; funding уточняется при закрытии.'
 note=f"PnL {pnl:+.2f} USDT ({pct:+.2f}%) · доля капитала {share} · qty {qty:.7g}\n{costs} Почему вход: {why}"
 if p.get('event_reason'):note+='\nПричина события: '+str(p['event_reason'])
 note+='\nВремя снимка: '+datetime.fromtimestamp(media['when']/1000,timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S МСК')+' · бумажная торговля'
 fig.text(.06,.035,'\n'.join(textwrap.fill(line,135) for line in note.splitlines()),fontsize=10,color='#d1dcea',va='bottom')
 fig.subplots_adjust(left=.06,right=.98,top=.9,bottom=.25)
 dest=Path(destination);dest.parent.mkdir(parents=True,exist_ok=True)
 tmp=dest.with_suffix('.tmp');fig.savefig(tmp,format='png',facecolor=fig.get_facecolor());plt.close(fig);os.replace(tmp,dest);return dest

def summary(media,destination):
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from datetime import datetime,timezone,timedelta
 positions=media['positions'];fig,ax=plt.subplots(figsize=(16,1.7+len(positions)*.5),dpi=120)
 fig.patch.set_facecolor('#101827');ax.set_facecolor('#101827');ax.axis('off')
 headers=['Профиль / монета / ТФ','Сторона','Вход','Стоп','Цель','Цена','PnL USDT','PnL %','Капитал %']
 rows=[];colors=[]
 for p in positions:
  mark=p.get('mark') or p['entry'];side=1 if p['side']=='long' else -1
  pnl=p.get('realized',0)+side*(mark-p['entry'])*p['qty']-p.get('fee_in',0)-mark*p['qty']*.0005
  from src.trade.position_metrics import metrics
  pm=metrics(p,p.get('equity_at_snapshot',0));share=f"{pm['exposure_pct']:.2f}%" if pm['exposure_pct'] is not None else '—'
  symbol=p['symbol'] if p['symbol'].isascii() else ascii(p['symbol'])[1:-1]
  rows.append([p['profile'].replace('screener-','')+' / '+symbol+' / '+p['tf'],p['side'].upper(),f"{p['entry']:.7g}",f"{p['stop']:.7g}",'трейлинг' if p.get('no_target') or not p.get('target') else f"{p['target']:.7g}",f'{mark:.7g}',f'{pnl:+.2f}',f"{pm['pnl_pct']:+.2f}%",share])
  colors.append('#39d7a0' if pnl>=0 else '#ff6476')
 table=ax.table(cellText=rows,colLabels=headers,colWidths=[.25,.07,.095,.095,.095,.095,.10,.10,.10],bbox=(0,0,1,1),cellLoc='left')
 table.auto_set_font_size(False);table.set_fontsize(10)
 for (r,c),cell in table.get_celld().items():
  cell.set_facecolor('#20314a' if r==0 else '#142034');cell.set_edgecolor('#34465e');cell.set_text_props(color=colors[r-1] if r and c in (6,7) else '#e6eef8')
 fig.text(.03,.95,f"Открытые бумажные позиции · лист {media['page']} из {media['pages']}",fontsize=19,color='#e6eef8')
 stamp=datetime.fromtimestamp(media['when']/1000,timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S МСК')
 fig.text(.03,.04,f'{stamp}\nНезависимые профили. PnL с частичными выходами и комиссиями; funding уточняется при закрытии.',fontsize=11,color='#ccd8e8')
 fig.subplots_adjust(left=.03,right=.98,top=.85,bottom=.16)
 dest=Path(destination);dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix('.tmp');fig.savefig(tmp,format='png',facecolor=fig.get_facecolor());plt.close(fig);os.replace(tmp,dest);return dest
