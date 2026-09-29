import json, re
from datetime import datetime, timezone
a = json.load(open("catalog48_all.json"))
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
PAREN = re.compile(r"\(([^()]{1,20})\)")
def ok(tk):
    tk = tk.strip()
    if not tk: return None
    if re.fullmatch(r"\d{4}(-\d{2}-\d{2})?", tk): return None      # дата
    if tk.isdigit(): return None
    if not re.fullmatch(r"[A-Za-z0-9]{1,15}", tk): return None     # 牛来 и пр. отсеиваются
    return tk.upper()
rows=[]
for x in a:
    t = x["title"]
    if "Will List" not in t: continue
    if "Leveraged Token" in t or "Options" in t or t.startswith("Binance Futures") or t.startswith("Binance Margin"): continue
    toks=[ok(tk) for tk in PAREN.findall(t)]
    toks=[tk for tk in toks if tk]
    if toks: rows.append((x["releaseDate"], t, toks))
tot=sum(len(r[2]) for r in rows)
print("Tier A: записей", len(rows), "монет", tot)
for ms,t,toks in rows:
    print(f"  {f(ms)}  {toks}  <- {t[:100]}")
