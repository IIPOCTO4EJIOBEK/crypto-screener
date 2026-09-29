import json, re, collections
from datetime import datetime, timezone
a = json.load(open("catalog48_all.json"))
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
PAREN = re.compile(r"\(([A-Z0-9]{2,15})\)")
nosk=[]
rows=[]
for x in a:
    t = x["title"]
    if "Will List" not in t: continue
    if "Leveraged Token" in t or "Options" in t or t.startswith("Binance Futures") or t.startswith("Binance Margin"): 
        continue
    toks = [tk for tk in PAREN.findall(t) if not tk.isdigit()]
    if toks:
        rows.append((x["id"], x["releaseDate"], t, toks)); continue
    nosk.append((x["id"], x["releaseDate"], t))
print("=== без скобочного тикера (кандидаты на ручной разбор), n =", len(nosk), "===")
for i,(_,ms,t) in enumerate(nosk): print(f"  {f(ms)}  {t}")
print()
print("=== с тикером, n =", len(rows), " монет:", sum(len(r[3]) for r in rows), "===")
# tokens that look like leveraged/derivative or suspicious
allt=[tk for r in rows for tk in r[3]]
sus=[tk for tk in allt if tk.endswith(("UP","DOWN","BULL","BEAR"))]
print("подозрительные (UP/DOWN):", sorted(set(sus)))
print("дубли монет:", [k for k,c in collections.Counter(allt).items() if c>1][:20])
