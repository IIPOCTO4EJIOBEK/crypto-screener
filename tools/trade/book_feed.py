"""Public futures partial-depth feed for paper entries, isolated from exits."""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path
import aiohttp
from src.trade.book_cache import decode

WS='wss://fstream.binance.com/public/stream?streams='

def write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,separators=(',',':')));os.replace(tmp,path)

def symbols(path):
    data=json.loads(path.read_text())
    held=[];pin=os.environ.get('SCREENER_POSITION_SYMBOLS')
    if pin:held=json.loads(Path(pin).read_text())['symbols']
    return sorted({s.upper() for s in list(data['symbols'])+held if s.upper().endswith('USDT')})

async def group(http, syms, out):
    while True:
        try:
            async with http.ws_connect(WS+'/'.join(s.lower()+'@depth20@500ms' for s in syms),heartbeat=20) as ws:
                # Reconnection starts a new ordering epoch. Old disk files expire by timestamp.
                last={};dirty={};flushed=0
                while True:
                    msg=await asyncio.wait_for(ws.receive(),30)
                    if msg.type != aiohttp.WSMsgType.TEXT: raise RuntimeError('depth socket closed')
                    d=json.loads(msg.data).get('data',{});s=d.get('s')
                    if s not in syms or d.get('e')!='depthUpdate': continue
                    d['received']=time.time();decode(d,s,d['received'])
                    update=int(d['u'])
                    if update<=last.get(s,0): continue
                    last[s]=update;dirty[s]=d
                    if time.monotonic()-flushed>=.5:
                        for symbol,data in dirty.items():write(out/(symbol+'.json'),data)
                        dirty.clear();flushed=time.monotonic()
        except asyncio.CancelledError: raise
        except Exception as exc:
            print('book feed reconnect:',type(exc).__name__,flush=True)
            await asyncio.sleep(3)

async def main(universe,out):
    out.mkdir(parents=True,exist_ok=True)
    async with aiohttp.ClientSession(trust_env=False) as http:
        tasks=[];current=None
        try:
            while True:
                wanted=symbols(universe)
                if wanted!=current:
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    current=wanted
                    tasks=[asyncio.create_task(group(http,wanted[i:i+60],out)) for i in range(0,len(wanted),60)]
                    print('book feed symbols:',len(wanted),flush=True)
                now=time.time();ages=[]
                for s in wanted:
                    try:
                        d=json.loads((out/(s+'.json')).read_text());decode(d,s,now);ages.append(now-d['E']/1000)
                    except (OSError,ValueError,KeyError,TypeError): pass
                write(out/'status.json',dict(updated_ms=int(now*1000),symbols=len(wanted),fresh=len(ages),max_age_seconds=max(ages,default=None)))
                await asyncio.sleep(5)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--universe',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    asyncio.run(main(a.universe,a.out))
