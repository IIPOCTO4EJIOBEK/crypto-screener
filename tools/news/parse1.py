import json, re, collections
from datetime import datetime, timezone
a = json.load(open("catalog48_all.json"))
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
pat = re.compile(r"\(([A-Z0-9]{2,15})\)")
occ=collections.Counter()
for x in a:
    if "Binance Will List" in x["title"]: occ[f(x["releaseDate"])[:4]]+=1
print("Binance Will List по годам:", dict(sorted(occ.items())))
print("всего:", sum(occ.values()))
print()
# sample older ones
old=[x["title"] for x in a if "Binance Will List" in x["title"] and x["releaseDate"]<1600000000000]
print("примеры 2020 года и ранее:")
for t in old[:25]: print("  ", t)
print("...всего таких:", len(old))
