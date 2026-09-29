import json, statistics as st
from datetime import datetime, timezone
D = json.load(open("listings_measured.json"))
S = [c for c in D["coins"] if not c.get("reason") and not c["data"]["1h"].get("pre_existing")]
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")

# 0) сверка: high первой 1h-свечи == максимум 15m внутри того же часа?
def m15_high(c):
    d=c["data"]["15m"]; t0=d["t0"]; base=d["base"]
    return d["windows"]["1h"]["max_ret"]
same = sum(1 for c in S if abs(c["data"]["1h"]["windows"]["1h"]["max_ret"] - m15_high(c)) < 1e-9)
print(f"совпадение максимума 1h-свечи и 15m-свечей внутри первого часа: {same}/{len(S)}")
bigger = sum(1 for c in S if m15_high(c) > c["data"]["1h"]["windows"]["1h"]["max_ret"] + 1e-9)
print(f"15m даёт больший максимум: {bigger}")

# 1) исполнимая доходность: вход = закрытие первой свечи
IV = {"15m": 900_000, "1h": 3_600_000}
def realized(c, tf, h, entry_kind):
    d = c["data"][tf]; s = d["series"] if "series" in d else None
    return None
# пересчитываем из сохранённых окон нельзя -> берём close первой свечи из окна 1h
print()
print("=== вход по ЗАКРЫТИЮ первой 1h-свечи (то, что реально можно купить) ===")
for kind, lbl in (("max_ret","максимум"),("min_ret","минимум"),("close_ret","закрытие")):
    for h in ("1h","4h","24h","72h","120h"):
        v=[]
        for c in S:
            w=c["data"]["1h"]["windows"].get(h)
            if not w: continue
            e = c["data"]["1h"]["windows"]["1h"]["close_ret"]  # close1/base - 1
            base = c["data"]["1h"]["base"]
            close1 = base*(1+e)
            v.append(w[kind] if kind!="close_ret" else w["close_ret"])
        # для входа по close1 нужно отношение (close_h_close1)/close1
        pass
print()
print("=== метрики от ЗАКРЫТИЯ первого часа (исполнимый вход) ===")
def desc(v):
    v=sorted(v); n=len(v)
    if not n: return None
    k=max(1,int(n*0.05)); tm=st.mean(v[k:n-k]) if n>2*k else st.mean(v)
    return (n, st.median(v), st.mean(v), tm, v[int(.25*(n-1))], v[int(.75*(n-1))],
            sum(1 for x in v if x>0)/n)
for tf in ("1h","15m"):
    print(f"-- {tf} --")
    for kind,lbl in (("max_ret","рост до максимума"),("min_ret","падение до минимума"),("close_ret","к закрытию")):
        for h in ("1h","4h","24h","72h","120h"):
            v=[]
            for c in S:
                d=c["data"][tf]; w=d["windows"].get(h); w1=d["windows"]["1h"]
                if not w or not w1: continue
                e1 = w1["close_ret"]           # close1/base - 1
                fac = 1/ (1+e1)                # base/close1
                v.append((1+w[kind])*fac - 1)
            r=desc(v)
            if r: print(f"   {h:5} {lbl:20} n={r[0]:3} медиана {r[1]:+8.1%} среднее {r[2]:+9.1%} 5%%трим {r[3]:+8.1%} p25 {r[4]:+8.1%} p75 {r[5]:+9.1%} доля>0 {r[6]:5.1%}")
    print()
# 2) «продавай на новости» от закрытия первого часа
print("=== продавай на новости (вход = закрытие 1-й 1h-свечи) ===")
for h in ("1h","4h","24h","72h","120h"):
    v=[]
    for c in S:
        d=c["data"]["1h"]; w=d["windows"].get(h); w1=d["windows"]["1h"]
        if not w or not w1: continue
        v.append((1+w["close_ret"])/(1+w1["close_ret"]) - 1)
    up=sum(1 for x in v if x>0)
    print(f"  {h:5}: выше {up} ({up/len(v):.1%}), ниже {len(v)-up} ({(len(v)-up)/len(v):.1%}), n={len(v)}, медиана {st.median(v):+.1%}, среднее {st.mean(v):+.1%}")
