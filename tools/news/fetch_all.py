import sys, json, time
from datetime import datetime, timezone
sys.path.insert(0, "../..")
from src.data import market
URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
def page(n, cat, size=50):
    return market._get(URL, {"type":1,"pageNo":n,"pageSize":size,"catalogId":cat}, use_proxy=True)
out, seen, fails = [], set(), 0
for n in range(1, 52):
    arts=None
    for attempt in range(4):
        try:
            arts = page(n, 48)["data"]["catalogs"][0]["articles"]; break
        except Exception as e:
            time.sleep(1.0+attempt)
    if not arts:
        print(f"page {n}: FAILED"); fails+=1; break
    new = [a for a in arts if a["id"] not in seen]
    for a in new: seen.add(a["id"]); out.append(a)
    time.sleep(0.2)
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
print(f"catalog 48: {len(out)} записей, {f(out[-1]['releaseDate'])} .. {f(out[0]['releaseDate'])}")
json.dump(out, open("catalog48_all.json","w"), ensure_ascii=False)
print("saved catalog48_all.json")
