import json, statistics as st
D = json.load(open("listings_measured.json"))
S = [c for c in D["coins"] if not c.get("reason") and not c["data"]["1h"].get("pre_existing")]
def desc(v):
    v = sorted(v); n=len(v)
    if n==0: return None
    k=max(1,int(n*0.05))
    tm = st.mean(v[k:n-k]) if n>2*k else st.mean(v)
    return dict(n=n, mean=st.mean(v), med=st.median(v), trimmed=tm,
                p25=v[int(0.25*(n-1))], p75=v[int(0.75*(n-1))],
                mn=v[0], mx=v[-1], pos=sum(1 for x in v if x>0)/n)
def line(name, d):
    if not d: print(f"{name:34}  нет данных"); return
    print(f"{name:34} n={d['n']:3}  медиана {d['med']:+7.1%}  среднее {d['mean']:+8.1%}  "
          f"среднее5% {d['trimmed']:+7.1%}  p25 {d['p25']:+7.1%}  p75 {d['p75']:+8.1%}  "
          f"доля>0 {d['pos']:5.1%}  min {d['mn']:+7.1%}  max {d['mx']:+8.1%}")
WIN = {"15m": ["1h","4h","24h","72h"], "1h": ["1h","4h","24h","72h","120h"]}
for tf in ("1h","15m"):
    print(f"########## {tf} ##########")
    for kind in ("max_ret","min_ret","close_ret"):
        lbl = {"max_ret":"рост до максимума","min_ret":"падение до минимума","close_ret":"к закрытию"}[kind]
        print(f"-- {lbl} --")
        for w in WIN[tf]:
            v=[c["data"][tf]["windows"][w][kind] for c in S if c["data"][tf].get("windows",{}).get(w)]
            line(f"{w} {kind}", desc(v))
    print()
# «продавай на новости»
print("########## продавай на новости ##########")
for tf in ("1h",):
    for w in ("24h","72h","120h"):
        v=[(c["ticker"], c["data"][tf]["windows"][w]["close_ret"]) for c in S if c["data"][tf].get("windows",{}).get(w)]
        up=sum(1 for _,r in v if r>0); dn=sum(1 for _,r in v if r<0); z=len(v)-up-dn
        print(f"{w}: выше цены открытия {up} ({up/len(v):.1%}), ниже {dn} ({dn/len(v):.1%}), ровно {z}, n={len(v)}")
for w in ("24h","72h"):
    # от цены ПЕРВОЙ СВЕЧИ, но считая от максимума окна — сколько откатилось от пика
    v=[c["data"]["1h"]["windows"][w] for c in S if c["data"]["1h"].get("windows",{}).get(w)]
    draw=[x["close_ret"]-x["max_ret"] for x in v]
    print(f"откат от максимума к закрытию {w}: медиана {st.median(draw):+.1%}, среднее {st.mean(draw):+.1%}, n={len(draw)}")
