from pathlib import Path
import json,shlex,datetime,hashlib,time,argparse
from deploy_server import connect,run,HOST
r=Path(__file__).resolve().parents[1];ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,default=r/'deployment/published/kronos-prospective.json');ap.add_argument('--out',type=Path,default=r/'deployment/published/kronos-prospective-verification.json');args=ap.parse_args();source=args.source;saved=json.loads(source.read_text('utf-8'));keys=sorted({f['symbol']+'_'+f['tf'] for f in saved['forecasts']});s=connect(HOST,'root')
code='import json,pathlib;root=pathlib.Path("/opt/crypto-screener/docs/live/kl");keys='+repr(keys)+';print(json.dumps({k:json.loads((root/(k+".json")).read_text()) for k in keys}))'
try:actual=json.loads(run(s,'python3 -c '+shlex.quote(code),quiet=True))
finally:s.close()
results=[]
for f in saved['forecasts']:
 candles={int(c[0]):c for c in actual[f['symbol']+'_'+f['tf']]};duration={'5m':300,'15m':900,'1h':3600}[f['tf']];pairs=[];excluded=0
 for stamp,pred in zip(f['times'],f['close']):
  ts=int(datetime.datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp());c=candles.get(ts*1000)
  if not c or f['created_unix']>=ts+duration or ts+duration>time.time():excluded+=1;continue
  pairs.append((pred,c[4]))
 if not pairs:continue
 ref=f['reference'];mae=sum(abs(p-a) for p,a in pairs)/len(pairs)/ref*100;naive=sum(abs(ref-a) for p,a in pairs)/len(pairs)/ref*100
 results.append(dict(model=f['model'],symbol=f['symbol'],tf=f['tf'],points=len(pairs),excluded=excluded,mae_pct=round(mae,5),last_price_mae_pct=round(naive,5),beats_last_price=mae<naive,endpoint_direction_correct=(pairs[-1][0]-ref)*(pairs[-1][1]-ref)>0,revision=f['revision'],created_unix=f['created_unix']))
out=dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),orders_enabled=False,forecasts=len(results),unique_symbol_tf_windows=len({(x['symbol'],x['tf']) for x in results}),results=results)
args.out.write_text(json.dumps(out,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(out,ensure_ascii=False))

