"""Fixed-parameter chronological validation; never sends orders."""
from pathlib import Path
import sys,os,json,subprocess,time,gc,argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'Kronos'))
import numpy as np,pandas as pd,torch
from model import Kronos,KronosTokenizer,KronosPredictor
ap=argparse.ArgumentParser();ap.add_argument('--tag',default='evaluation-20261006');ap.add_argument('--symbols',default='BTCUSDT,ETHUSDT,SOLUSDT');ap.add_argument('--tfs',default='15m,1h');ap.add_argument('--origins',type=int,default=24);ap.add_argument('--validation',type=int,default=8);args=ap.parse_args()
assert '/' not in args.tag and '\\' not in args.tag and args.tag not in ['.','..']
symbols=args.symbols.split(',');tfs=args.tfs.split(',');minutes={'5m':5,'15m':15,'1h':60};assert set(tfs)<=set(minutes)
OUT=ROOT/'Kronos/outputs'/args.tag;OUT.mkdir(parents=True,exist_ok=True)
manifest=json.loads((ROOT/'Kronos/weights/manifest.json').read_text())
H=8;CONTEXT=400;ORIGINS=args.origins;VAL=args.validation;FEE=.0005;SLIP=.0001;THRESHOLD=2*(FEE+SLIP);assert 0<=VAL<ORIGINS
def fetch(path,params):
 url='https://fapi.binance.com'+path+'?'+'&'.join(f'{k}={v}' for k,v in params.items())
 raw=subprocess.check_output(['ssh','-o','BatchMode=yes','root@72.56.75.237',f"curl --fail -sS --max-time 30 '{url}'"])
 return json.loads(raw)
data={};fundings={}
for symbol in symbols:
 for tf in tfs:
  p=OUT/f'{symbol}_{tf}_input.csv'
  if not p.exists():
   rows=fetch('/fapi/v1/klines',dict(symbol=symbol,interval=tf,limit=1000))[:-1]
   df=pd.DataFrame([dict(timestamps=pd.to_datetime(r[0],unit='ms'),open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]),volume=float(r[5]),amount=float(r[7])) for r in rows]);df.to_csv(p,index=False)
  df=pd.read_csv(p,parse_dates=['timestamps']);assert len(df)>=CONTEXT+H*ORIGINS
  delta=pd.Timedelta(minutes=minutes[tf])
  assert (df.timestamps.diff().dropna()==delta).all()
  data[(symbol,tf)]=df
 p=OUT/f'{symbol}_funding.json'
 if not p.exists():p.write_text(json.dumps(fetch('/fapi/v1/fundingRate',dict(symbol=symbol,startTime=int(min(data[(symbol,t)].timestamps.iloc[-ORIGINS*H].value//10**6 for t in tfs)),limit=1000))))
 fundings[symbol]=json.loads(p.read_text())
print('Six datasets cached; starting CUDA validation.',flush=True)
assert torch.cuda.is_available();tokenizer=KronosTokenizer.from_pretrained(str(ROOT/'Kronos/weights/Kronos-Tokenizer-base'))
all_results=[];live=[]
for modelname in ['small','base']:
 model=Kronos.from_pretrained(str(ROOT/f'Kronos/weights/Kronos-{modelname}'))
 predictor=KronosPredictor(model,tokenizer,device='cuda:0',max_context=512)
 for (symbol,tf),df in data.items():
  results=[];origins=range(len(df)-ORIGINS*H,len(df)-H+1,H)
  for k,start in enumerate(origins):
   history=df.iloc[start-CONTEXT:start];truth=df.iloc[start:start+H];seed=42+k
   torch.manual_seed(seed);np.random.seed(seed)
   pred=predictor.predict(df=history[['open','high','low','close','volume','amount']],x_timestamp=history.timestamps,y_timestamp=truth.timestamps,pred_len=H,sample_count=3,verbose=False)
   assert np.isfinite(pred.to_numpy()).all()
   ref=float(history.close.iloc[-1]);entry=float(truth.open.iloc[0]);exit=float(truth.close.iloc[-1]);prediction=float(pred.close.iloc[-1])/ref-1
   side=int(np.sign(prediction)) if abs(prediction)>THRESHOLD else 0
   momentum=float(history.close.iloc[-1]/history.close.iloc[-33]-1);base_side=int(np.sign(momentum)) if abs(momentum)>THRESHOLD else 0
   start_ms=int(truth.timestamps.iloc[0].value//10**6);end_ms=int((truth.timestamps.iloc[-1]+pd.Timedelta(minutes=minutes[tf])).value//10**6)
   funding=sum(float(r['fundingRate']) for r in fundings[symbol] if start_ms < int(r['fundingTime']) <= end_ms)
   actual=exit/entry-1
   def net(direction):return direction*actual-abs(direction)*(FEE+SLIP)*(1+exit/entry)-direction*funding
   row=dict(model=modelname,symbol=symbol,tf=tf,split='validation' if k<VAL else 'test',origin=str(history.timestamps.iloc[-1]),entry_time=str(truth.timestamps.iloc[0]),end_time=str(truth.timestamps.iloc[-1]),seed=seed,predicted_return=prediction,actual_return=actual,side=side,kronos_net=net(side),momentum_net=net(base_side),flat_net=0.0,last_price_mae=float(np.abs(truth.close.to_numpy()-ref).mean()/ref),kronos_mae=float(np.abs(pred.close.to_numpy()-truth.close.to_numpy()).mean()/ref),funding_rate_sum=funding)
   results.append(row);all_results.append(row)
  pd.DataFrame(results).to_csv(OUT/f'{symbol}_{tf}_{modelname}_scores.csv',index=False)
  print(modelname,symbol,tf,'test net sum',round(sum(r['kronos_net'] for r in results[VAL:])*100,3),'%',flush=True)
  # Fresh prospective forecast is stored separately from retrospective scores.
  hist=df.iloc[-CONTEXT:];delta=pd.Timedelta(minutes=minutes[tf]);future=pd.Series(pd.date_range(hist.timestamps.iloc[-1]+delta,periods=H,freq=delta))
  torch.manual_seed(42);np.random.seed(42)
  pred=predictor.predict(df=hist[['open','high','low','close','volume','amount']],x_timestamp=hist.timestamps,y_timestamp=future,pred_len=H,sample_count=3,verbose=False)
  live.append(dict(model=modelname,symbol=symbol,tf=tf,created_unix=time.time(),last_closed_candle=str(hist.timestamps.iloc[-1])+'Z',valid_until_unix=float((future.iloc[-1]+delta).value/1e9),revision=manifest['Kronos-'+modelname]['revision'],sample_count=3,seed=42,reference=float(hist.close.iloc[-1]),times=[str(t)+'Z' for t in future],close=[float(v) for v in pred.close]))
 del predictor,model;gc.collect();torch.cuda.empty_cache()
summary=[]
for modelname in ['small','base']:
 for symbol,tf in data:
  rs=[r for r in all_results if r['model']==modelname and r['symbol']==symbol and r['tf']==tf and r['split']=='test']
  summary.append(dict(model=modelname,symbol=symbol,tf=tf,n=len(rs),trades=sum(r['side']!=0 for r in rs),kronos_compounded_pct=(np.prod([1+r['kronos_net'] for r in rs])-1)*100,momentum_compounded_pct=(np.prod([1+r['momentum_net'] for r in rs])-1)*100,flat_pct=0,kronos_mae=np.mean([r['kronos_mae'] for r in rs]),last_price_mae=np.mean([r['last_price_mae'] for r in rs])))
report=dict(created_unix=time.time(),gpu=torch.cuda.get_device_name(0),model_revisions=manifest,parameters=dict(context=CONTEXT,horizon=H,origins=ORIGINS,validation_origins=VAL,test_origins=ORIGINS-VAL,samples=3,fee_each=FEE,slippage_each=SLIP,threshold=THRESHOLD),limitations=['Small recent sample; not proof of profitability.','Frozen pretrained models; no fine tuning or parameter selection.','Funding approximates mark-price notional by entry notional.','OHLCV next-open fills plus fixed slippage; no historical L2 execution.','32-bar momentum is a simple baseline, not the existing daily 28/5 strategy.','Prospective forecast validity is measured from candle timestamps; stale files must be ignored.'],scores=summary)
(OUT/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8');(OUT/'prospective.json').write_text(json.dumps(dict(experimental=True,orders_enabled=False,generated_unix=time.time(),forecasts=live),indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2),flush=True)

