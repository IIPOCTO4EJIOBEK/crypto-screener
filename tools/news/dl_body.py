import sys, json, re, time
sys.path.insert(0, "../..")
from src.data import market
a = json.load(open("announcements.json"))["161"]
import collections
c=collections.Counter()
for x in a:
    t=re.sub(r"\s*-?\s*\d{4}-\d{2}-\d{2}\s*$","",x["title"]).strip()
    c[re.sub(r"\d+","N",t)]+=1
for t,n in c.most_common(12): print(f"{n:4}  {t[:110]}")
print()
x = [y for y in a if y["title"].startswith("Notice of Removal of Spot Trading Pairs")][0]
print("пример:", x["title"], x["code"])
d = market._get("https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query",
                {"articleCode": x["code"]}, use_proxy=True)
txt = json.dumps(d, ensure_ascii=False)
print("размер ответа:", len(txt))
# ищем символы вида XXXUSDT / XXX/USDT / XXXBTC
syms = sorted(set(re.findall(r"\b([A-Z0-9]{2,12})/(USDT|BTC|FDUSD|TRY|BUSD)\b", txt)))
print("пар в теле:", len(syms), syms[:40])
