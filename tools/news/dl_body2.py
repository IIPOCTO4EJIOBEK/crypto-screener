import sys, json, re
sys.path.insert(0, "../..")
from src.data import market
a = json.load(open("announcements.json"))["161"]
x = [y for y in a if y["title"].startswith("Notice of Removal of Spot Trading Pairs")][0]
d = market._get("https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query",
                {"articleCode": x["code"]}, use_proxy=True)
def walk(o, out):
    if isinstance(o, dict):
        for k,v in o.items():
            if k in ("body","text","content","title") and isinstance(v,str): out.append((k,v))
            else: walk(v,out)
    elif isinstance(o,list):
        for v in o: walk(v,out)
out=[]; walk(d,out)
for k,v in out[:6]:
    txt = v if len(v)<3000 else v[:3000]+"..."
    print(f"### {k} (len {len(v)})")
    # вытащим текст из rich-text
    print(re.sub(r"<[^>]+>"," ", txt)[:2000].replace("\\u",""))
    print()
