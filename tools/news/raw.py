import sys
from datetime import datetime, timezone
sys.path.insert(0, "../..")
from src.data import market
d = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
db = json.load if False else None
import json
D=json.load(open("listings_measured.json"))
by={c["ticker"]:c for c in D["coins"]}
for tk in ("FIL","APT","GLMR","TNSR","ORDI"):
    c=by[tk]; am=c["announce_ms"]; d1=c["data"]["1h"]
    print(f"--- {tk}  анонс {d(am)}  t0 {d(d1['t0'])}  base(open 1-й свечи)={d1['base']}")
    s=market.ohlcv("binance", tk+"USDT", "1h", 1000, end_ms=am+13*86400_000)
    for k in s[:6]:
        print(f"     {d(k.ts)}  O {k.open:<12.8g} H {k.high:<12.8g} L {k.low:<12.8g} C {k.close:<12.8g} qvol {k.quote_volume:,.0f}")
    print(f"     ... всего свечей {len(s)}, последняя {d(s[-1].ts)}")
