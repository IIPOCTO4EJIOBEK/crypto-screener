from pathlib import Path
import json,time,uuid,math
from src.trade.atomic_store import read_state
from src.trade.photo_reports import chart,capture
root=Path('/opt/crypto-trade/data/trade');now=int(time.time()*1000);positions=[];media=[]
for p in sorted(root.glob('screener-*/execution.db')):
 state=read_state(p.parent)
 equity=state['cash']+sum((1 if q['side']=='long' else -1)*q['qty']*((q.get('mark') or q['entry'])-q['entry']) for q in state['positions'].values())
 for pos in state['positions'].values():positions.append(dict(pos,profile=p.parent.name,equity_at_snapshot=equity))
pages=math.ceil(len(positions)/8)
for i in range(pages):media.append(dict(kind='summary',positions=positions[i*8:(i+1)*8],when=now,page=i+1,pages=pages,caption=f'Открытые бумажные позиции: лист {i+1}/{pages}, всего {len(positions)}. PnL в USDT; независимые профили.'))
chosen=sorted(positions,key=lambda p:(p['symbol'] not in ['USUSDT','GTCUSDT','API3USDT'],p['profile']!='screener-managed'))[:2]
for p in chosen:
 media.append(dict(kind='position',position=p,profile=p['profile'],event='snapshot',when=now,candles=capture(p['symbol'],p['tf'],now),caption=f"Позиция {p['symbol']} {p['tf']} {p['side']} · {p['profile']}. Вход/стоп/цель и причина входа на графике. Бумажная торговля."))
out=root/'screener-managed/reports';out.mkdir(exist_ok=True);manifest=[]
for i,m in enumerate(media):
 ident=uuid.uuid4().hex;path=out/(ident+'.png');chart(m,path);manifest.append(dict(id=ident,media=m,path=str(path)));print('Rendered',i+1,m['kind'],flush=True)
(out/'report-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False));print('Prepared',len(manifest),'images',len(positions),'positions',flush=True)
