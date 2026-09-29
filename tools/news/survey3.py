import json, re, collections
a48 = json.load(open("announcements.json"))["48"]
print("=== 'Notice on New Trading Pairs' raw titles (первые 20) ===")
sel=[x["title"] for x in a48 if "Notice on New Trading Pairs" in x["title"]]
for t in sel[:20]: print("  ", t)
print("всего таких:", len(sel))
print()
print("=== 'Trading Bots Services' raw (первые 12) ===")
sel2=[x["title"] for x in a48 if "Trading Bots Services on Binance Spot" in x["title"] and "New Trading Pairs" not in x["title"]]
for t in sel2[:12]: print("  ", t)
print("всего:", len(sel2))
print()
print("=== прочие 'Will Add' / 'Will Support' ===")
sel3=[x["title"] for x in a48 if ("Will Add" in x["title"] or "Adds New" in x["title"]) and "Margin" not in x["title"]]
for t in sel3[:15]: print("  ", t)
print("всего:", len(sel3))
print()
# сколько записей вообще содержат скобочный тикер
pat = re.compile(r"\(([A-Z0-9]{2,15})\)")
c=0
for x in a48:
    if pat.findall(x["title"]): c+=1
print("записей с (TICKER) в заголовке:", c, "из", len(a48))
