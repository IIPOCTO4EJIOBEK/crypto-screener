import json, statistics as st
from datetime import datetime, timezone
D = json.load(open("listings_measured.json"))
coins = D["coins"]
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
ok  = [c for c in coins if not c.get("reason")]
noS = [c for c in coins if c.get("reason","").startswith("нет пары")]
late= [c for c in coins if "появилась только" in c.get("reason","")]
print(f"всего монет {len(coins)}; измеряемых {len(ok)}; без пары USDT {len(noS)}; пара появилась позже {len(late)}")
print("без пары USDT:", [c['ticker'] for c in noS])
print()
pre=[c for c in ok if c["data"]["1h"].get("pre_existing")]
print("pre_existing (символ торговался до анонса):", [(c['ticker'], f(c['announce_ms'])) for c in pre])
print()
lag = sorted(c["data"]["1h"]["lag_h"] for c in ok if not c["data"]["1h"].get("pre_existing"))
q = lambda p: lag[int(p*(len(lag)-1))]
print("лаг анонс→первая свеча (ч): n=%d  min %.2f p10 %.2f p25 %.2f med %.2f p75 %.2f p90 %.2f max %.1f" % (
    len(lag), lag[0], q(.1), q(.25), st.median(lag), q(.75), q(.9), lag[-1]))
print()
print("=== полнота окон ===")
for tf in ("1h","15m"):
    for w in ("1h","4h","24h","72h","120h"):
        cs=[c for c in ok if c["data"][tf].get("windows",{}).get(w)]
        comp=[c for c in cs if c["data"][tf]["windows"][w]["complete"]]
        print(f"  {tf:4} {w:5}: окно есть {len(cs):3}  полное {len(comp):3}")
