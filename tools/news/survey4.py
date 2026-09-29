import json, re, collections
a48 = json.load(open("announcements.json"))["48"]
pat = re.compile(r"\(([A-Z0-9]{2,15})\)")
alltok=collections.Counter(); kinds=collections.Counter(); samples={}
for x in a48:
    toks = pat.findall(x["title"])
    if not toks: continue
    t = x["title"]
    if "Binance Will List" in t: k="spot-list(Will List)"
    elif re.match(r"Binance Futures", t): k="futures"
    elif re.match(r"Binance Options", t): k="options"
    elif "bStock" in t: k="bStocks"
    elif "Margin" in t: k="margin"
    elif re.match(r"Binance (Adds|Exchange Adds|Will Add)", t): k="adds-pairs"
    else: k="other"
    kinds[k]+=1
    samples.setdefault(k, t)
    for tk in toks: alltok[tk]+=1
print("=== состав записей с (TICKER) ===")
for k,c in kinds.most_common(): print(f"{c:5}  {k:24} e.g. {samples[k][:90]}")
print()
# suspicious tokens
sus=[t for t in alltok if t.isdigit() or re.fullmatch(r"[0-9]+",t)]
print("числовые в скобках:", sorted(sus))
print("всего разных токенов:", len(alltok))
print()
print("=== 'other' sample ===")
c=0
for x in a48:
    toks=pat.findall(x["title"])
    if not toks: continue
    t=x["title"]
    if "Binance Will List" not in t and not re.match(r"Binance Futures",t) and not re.match(r"Binance Options",t) and "bStock" not in t and "Margin" not in t and not re.match(r"Binance (Adds|Exchange Adds|Will Add)",t):
        print("  ", t[:120]); c+=1
        if c>25: break
