import json, collections, csv, statistics as st
from datetime import datetime, timezone
D=json.load(open("listings_measured.json"))
S=[c for c in D["coins"] if not c.get("reason") and not c["data"]["1h"].get("pre_existing")]
f=lambda ms: datetime.fromtimestamp(ms/1000,tz=timezone.utc).strftime("%Y-%m")
g=collections.defaultdict(list)
for c in S: g[f(c["announce_ms"])[:4]].append(c)
print("год | n | медиана close72 (от открытия) | медиана close72 (от закр. 1ч) | доля>0 (от закр.1ч)")
for y in sorted(g):
    v1=[]; v2=[]
    for c in g[y]:
        d=c["data"]["1h"]; w=d["windows"]["72h"]
        v1.append(w["close_ret"]); v2.append((1+w["close_ret"])/(1+d["windows"]["1h"]["close_ret"])-1)
    print(f"  {y} | {len(g[y]):3} | {st.median(v1):+8.1%} | {st.median(v2):+8.1%} | {sum(1 for x in v2 if x>0)/len(v2):5.1%}")
# CSV
with open("listings_metrics.csv","w",newline="") as fh:
    w=csv.writer(fh)
    w.writerow(["ticker","tier","announce_utc","trading_open_utc","lag_h","open_price","t0_qvol_usdt",
                "from_open_max_1h","from_open_min_1h","from_open_close_1h",
                "from_open_max_24h","from_open_min_24h","from_open_close_24h",
                "from_open_max_72h","from_open_min_72h","from_open_close_72h",
                "from_c1_max_24h","from_c1_min_24h","from_c1_close_24h",
                "from_c1_max_72h","from_c1_min_72h","from_c1_close_72h"])
    iso=lambda ms: datetime.fromtimestamp(ms/1000,tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    for c in S:
        d=c["data"]["1h"]; W=d["windows"]; c1=W["1h"]["close_ret"]
        rel=lambda h,k: (1+W[h][k])/(1+c1)-1
        w.writerow([c["ticker"],c["tier"],iso(c["announce_ms"]),iso(d["t0"]),round(d["lag_h"],2),d["base"],
            round(d["first_qvol"]),round(W["1h"]["max_ret"],4),round(W["1h"]["min_ret"],4),round(W["1h"]["close_ret"],4),
            round(W["24h"]["max_ret"],4),round(W["24h"]["min_ret"],4),round(W["24h"]["close_ret"],4),
            round(W["72h"]["max_ret"],4),round(W["72h"]["min_ret"],4),round(W["72h"]["close_ret"],4),
            round(rel("24h","max_ret"),4),round(rel("24h","min_ret"),4),round(rel("24h","close_ret"),4),
            round(rel("72h","max_ret"),4),round(rel("72h","min_ret"),4),round(rel("72h","close_ret"),4)])
print("\nCSV: listings_metrics.csv, строк", len(S))
# сколько монет в каждой подвыборке по «исполнимости»
print("\nmonет с 120h данными:", sum(1 for c in S if c["data"]["1h"]["windows"].get("120h")))
