import json, statistics as st
from datetime import datetime, timezone
D = json.load(open("listings_measured.json"))
ALL = D["coins"]
S = [c for c in ALL if not c.get("reason") and not c["data"]["1h"].get("pre_existing")]
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
def desc(v):
    v=sorted(v); n=len(v)
    if not n: return None
    k=max(1,int(n*0.05)); tm=st.mean(v[k:n-k]) if n>2*k else st.mean(v)
    return dict(n=n, med=st.median(v), mean=st.mean(v), trim=tm,
                p25=v[int(.25*(n-1))], p75=v[int(.75*(n-1))], mn=v[0], mx=v[-1],
                pos=sum(1 for x in v if x>0)/n)
def row(lbl,d):
    if not d: print(f"  {lbl:26} нет данных"); return
    print(f"  {lbl:26} n={d['n']:3}  медиана {d['med']:+8.1%}  среднее {d['mean']:+9.1%}  "
          f"5%-трим {d['trim']:+8.1%}  p25 {d['p25']:+8.1%}  p75 {d['p75']:+8.1%}  "
          f"доля>0 {d['pos']:5.1%}  min {d['mn']:+8.1%}  max {d['mx']:+9.1%}")

# ---------- 1. сырые метрики от цены открытия торгов ----------
print("################ ОТ ЦЕНЫ ОТКРЫТИЯ ТОРГОВ (open первой свечи) ################")
for tf in ("1h",):
    for kind,lbl in (("max_ret","до максимума"),("min_ret","до минимума"),("close_ret","до закрытия")):
        print(f"--- {lbl} ({tf}) ---")
        for h in ("1h","4h","24h","72h","120h"):
            row(h, desc([c["data"][tf]["windows"][h][kind] for c in S if c["data"][tf]["windows"].get(h)]))
        print()

# ---------- 2. исполнимые метрики ----------
def rel(c, tf, h, kind, entry):
    d=c["data"][tf]; w=d["windows"].get(h)
    if not w: return None
    e = d["windows"]["1h"]["close_ret"] if entry=="close1h" else d["windows"]["1h"]["close_ret"]
    return (1+w[kind])/(1+e) - 1
print("################ ИСПОЛНИМО: ВХОД = ЗАКРЫТИЕ ПЕРВОГО ЧАСА ################")
for kind,lbl in (("max_ret","до максимума"),("min_ret","до минимума"),("close_ret","к закрытию")):
    print(f"--- {lbl} ---")
    for h in ("4h","24h","72h","120h"):
        row(h, desc([rel(c,"1h",h,kind,"close1h") for c in S if c["data"]["1h"]["windows"].get(h)]))
    print()
print("################ ИСПОЛНИМО: ВХОД = ЗАКРЫТИЕ ПЕРВЫХ 15 МИНУТ ################")
def rel15(c, h, kind):
    d=c["data"]["15m"]; w=d["windows"].get(h)
    if not w: return None
    e = d["windows"]["1h"]["close_ret"]
    return (1+w[kind])/(1+e) - 1
for kind,lbl in (("max_ret","до максимума"),("min_ret","до минимума"),("close_ret","к закрытию")):
    print(f"--- {lbl} ---")
    for h in ("1h","4h","24h","72h"):
        row(h, desc([rel15(c,h,kind) for c in S if c["data"]["15m"]["windows"].get(h)]))
    print()

# ---------- 3. продавай на новости ----------
print("################ ПРОДАВАЙ НА НОВОСТИ ################")
for entry in ("open","close1h","close15m"):
    print(f"-- вход: {entry} --")
    for h in (("24h","72h","120h") if entry!="close15m" else ("24h","72h")):
        v=[]
        for c in S:
            d=c["data"]["1h" if entry!="close15m" else "15m"]
            w=d["windows"].get(h)
            if not w: continue
            e = 0.0 if entry=="open" else d["windows"]["1h"]["close_ret"]
            v.append((1+w["close_ret"])/(1+e)-1)
        up=sum(1 for x in v if x>0)
        print(f"   {h:5}: выше {up:3} ({up/len(v):5.1%}), ниже {len(v)-up:3} ({(len(v)-up)/len(v):5.1%})  n={len(v)}  медиана {st.median(v):+7.1%}  среднее {st.mean(v):+8.1%}")
    print()

# ---------- 4. Messari: 5 дней ----------
print("################ СВЕРКА С MESSARI (+91% за 5 дней, разброс -32%..+645%) ################")
v_from_open = [c["data"]["1h"]["windows"]["120h"]["close_ret"] for c in S if c["data"]["1h"]["windows"].get("120h")]
v_from_c1 = [rel(c,"1h","120h","close_ret","close1h") for c in S if c["data"]["1h"]["windows"].get("120h")]
v_from_c15= [rel15(c,"72h","close_ret") for c in S if c["data"]["15m"]["windows"].get("72h")]
for lbl,v in (("от открытия (5 дн)",v_from_open),("от закрытия 1 ч (5 дн)",v_from_c1),("от закрытия 15 мин (3 дн)",v_from_c15)):
    d=desc(v)
    print(f"  {lbl:26} n={d['n']}  среднее {d['mean']:+.1%}  медиана {d['med']:+.1%}  min {d['mn']:+.1%}  max {d['mx']:+.1%}")
    print(f"      доля в диапазоне Messari [-32%,+645%]: {sum(1 for x in v if -0.32<=x<=6.45)/len(v):.1%}")
print()

# ---------- 5. подвыборки по годам ----------
print("################ ПО ГОДАМ (от открытия, close 72h) ################")
import collections
g=collections.defaultdict(list)
for c in S: g[f(c["announce_ms"])[:4]].append(c)
for y in sorted(g):
    v=[c["data"]["1h"]["windows"]["72h"]["close_ret"] for c in g[y] if c["data"]["1h"]["windows"].get("72h")]
    d=desc(v)
    print(f"  {y}: n={d['n']:3}  медиана {d['med']:+7.1%}  среднее {d['mean']:+9.1%}  доля>0 {d['pos']:5.1%}")

# ---------- 6. объём первой свечи ----------
print()
print("################ ОБЪЁМ ПЕРВОЙ СВЕЧИ (qvol, USDT) ################")
q=[c["data"]["1h"]["first_qvol"] for c in S]
q=sorted(q)
print(f"  n={len(q)} медиана {st.median(q):,.0f}  p25 {q[len(q)//4]:,.0f}  p75 {q[3*len(q)//4]:,.0f}  min {q[0]:,.0f}  max {q[-1]:,.0f}")
