import json, statistics as st
from datetime import datetime, timezone
D = json.load(open("listings_measured.json"))
coins = D["coins"]
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
print("всего монет:", len(coins))
nf = [c for c in coins if c.get("reason")]
print("без пары на Binance:", len(nf), [c["ticker"] for c in nf])
ok = [c for c in coins if not c.get("reason")]
print("с парой:", len(ok))
errs = [c for c in ok if any(isinstance(v,dict) and "error" in v for v in c["data"].values())]
print("ошибки свечей:", len(errs), [(c["ticker"], c["data"]) for c in errs][:5])
print()
# диагностика
pre = [c for c in ok if c["data"]["1h"].get("pre_existing")]
print("серия начинается задолго до анонса (pre_existing):", len(pre), [c["ticker"] for c in pre])
lags = sorted(c["data"]["1h"]["lag_h"] for c in ok if "lag_h" in c["data"]["1h"])
print("лаг анонс→первая свеча, ч: median %.2f  p25 %.2f  p75 %.2f  min %.2f  max %.1f" % (
    st.median(lags), lags[len(lags)//4], lags[3*len(lags)//4], lags[0], lags[-1]))
neg = [ (c["ticker"], round(c["data"]["1h"]["lag_h"],2)) for c in ok if c["data"]["1h"].get("lag_h",0) < -1]
print("лаг < -1 ч (свечи раньше анонса):", len(neg), neg[:10])
print()
# полнота окон
for tf in ("1h","15m"):
    for w in ("1h","4h","24h","72h","120h"):
        cs = [c for c in ok if c["data"][tf]["windows"].get(w)]
        comp = [c for c in cs if c["data"][tf]["windows"][w]["complete"]]
        print(f"  {tf} {w:5}: есть окно {len(cs):3}  полное {len(comp):3}")
