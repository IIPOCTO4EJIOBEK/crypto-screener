import json, re, collections
from datetime import datetime, timezone
a = json.load(open("catalog48_all.json"))
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
wl = [x for x in a if "Will List" in x["title"]]
ex = collections.Counter()
keep=[]
for x in wl:
    t=x["title"]
    if "Leveraged Token" in t: ex["Leveraged Tokens (UP/DOWN)"]+=1
    elif "Options" in t: ex["опционы"]+=1
    elif t.startswith("Binance Futures"): ex["фьючерсные квартальные контракты"]+=1
    elif t.startswith("Binance Margin"): ex["маржа"]+=1
    else: keep.append(x)
print("записей в catalog 48 всего:", len(a), f(a[-1]['releaseDate']), "..", f(a[0]['releaseDate']))
print("из них с 'Will List' в заголовке:", len(wl))
for k,v in ex.most_common(): print(f"   исключено — {k}: {v}")
print("остаётся анонсов спот-листинга:", len(keep))
D = json.load(open("listings_measured.json"))
print()
print("распознано записей с тикером:", sum(1 for c in D["coins"]))  # unique coins after dedup
print("отброшено записей без распознаваемого тикера:", len(D["dropped"]))
print("Tier B записей (тикер виден текстом, без скобок):", sum(1 for c in D["coins"] if c["tier"]=="B"))
print()
coins=D["coins"]
noreason=[c for c in coins if not c.get("reason")]
reason=collections.Counter()
for c in coins:
    r=c.get("reason","")
    if not r: continue
    if r.startswith("нет пары"): reason["нет пары USDT сейчас на Binance (делистинг/никогда не было)"]+=1
    elif "появилась только" in r: reason["пара USDT появилась позже анонса"]+=1
    else: reason[r[:60]]+=1
print("монет всего (после дедупликации по тикеру):", len(coins))
print("измеряемых:", len(noreason))
for k,v in reason.most_common(): print(f"   не измерено — {k}: {v}")
