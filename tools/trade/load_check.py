"""Isolated synthetic exit benchmark; never touches production profiles or orders."""
import json,time,tempfile,statistics,argparse
from pathlib import Path
from src.trade.intraday import BotState,Position,Config,cycle,load_state
from src.trade.atomic_store import transaction,read_journal
from src.trade.ledger import Ledger
from src.trade.monitor_market import MinuteCache

class Broker:
    live=False;fee=.0005

def benchmark(count=200,profiles=7,rounds=5):
    with tempfile.TemporaryDirectory(prefix='screener-load-') as temp:
        root=Path(temp);market=root/'kl';market.mkdir();now=int(time.time()*1000);start=now//60000*60000
        symbols=[f'LOAD{i}USDT' for i in range(count)];ledgers=[]
        for symbol in symbols:
            rows=[[start-120000,100,101,99,100,1],[start-60000,100,101,99,100,1],[start,100,101,99,100,1]]
            (market/(symbol+'_1m.json')).write_text(json.dumps(rows))
        for i in range(profiles):
            ledger=Ledger(root/f'profile{i}');state=BotState(cash=1000,peak=1000,start_equity=1000)
            for symbol in symbols:
                state.put(Position(key=symbol,symbol=symbol,kind='load',title='load',tf='5m',side='long',qty=.025,entry=100,signal_entry=100,stop=98,target=104,opened_ms=start-120000,expires_ms=2**62,fee_in=.00125,risk0=2,last_check_ms=start-120000))
            with transaction(ledger) as tx:tx.commit(state)
            ledgers.append(ledger)
        def run(price):
            begun=time.perf_counter();cache=MinuteCache(market,None,local_only=True);closed=0
            quotes={symbol:(price,now) for symbol in symbols}
            for ledger in ledgers:
                with transaction(ledger) as tx:
                    state=load_state(ledger.state_path,0,0)
                    result=cycle([],state,broker=Broker(),ledger=ledger,candles=cache.candles,now_ms=now,cfg=Config(no_timeout=True,funding=False,max_open=count,exit_on_opposite=True),live_prices=quotes)
                    closed+=result['closed'];tx.commit(state,result['events'],notify=True)
            return time.perf_counter()-begun,closed
        times=[]
        for _ in range(rounds):
            elapsed,closed=run(100);assert closed==0;times.append(elapsed)
        # A new observed quote must come after each saved last_tick_ms.
        now+=1000
        stop_seconds,closed=run(97);assert closed==count*profiles
        assert all(sum(r['kind']=='close' and r['reason']=='stop' for r in read_journal(l.root))==count for l in ledgers)
        return dict(synthetic=True,positions_per_profile=count,profiles=profiles,total=count*profiles,round_seconds=times,median_seconds=statistics.median(times),max_seconds=max(times),stop_batch_seconds=stop_seconds,closed_by_stop=closed,limitations=['Synthetic shared candles and quotes; no real exchange traffic.','Includes SQL commits, JSON projections and notification queue writes.','Excludes HTML rendering, funding settlement and photo sender; not an end-to-end SLA.'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path);a=p.parse_args();result=benchmark()
    if a.out:a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
