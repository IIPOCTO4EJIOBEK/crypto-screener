import json
D = json.load(open("listings_measured.json"))
for tk in ("NAS","GO","BTCB","BGBP","TUSDB","IDRT","SPARTA","EASY","BOT","HEGIC","COVER","VAI","GYEN","OLD"):
    for c in D["coins"]:
        if c["ticker"]==tk: print(f"{tk:8} tier {c['tier']}  <- {c['title'][:115]}")
print()
print("=== Tier B ===")
for c in D["coins"]:
    if c["tier"]=="B": print(f"  {c['ticker']:10} <- {c['title'][:110]}")
print()
print("=== отброшенные записи ===")
for x in D["dropped"]: print("  ", x["title"][:115])
