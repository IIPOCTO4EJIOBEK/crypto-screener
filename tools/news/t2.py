import sys, json, time
sys.path.insert(0, "../..")
from src.data import market
from datetime import datetime, timezone

def page(n, cat=48, size=50):
    return market._get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                {"type":1,"pageNo":n,"pageSize":size,"catalogId":cat}, use_proxy=True)

r = page(1)
arts = r["data"]["catalogs"][0]["articles"]
a = arts[0]
print("ARTICLE KEYS:", list(a.keys()))
print(json.dumps(a, ensure_ascii=False)[:1200])
print()
for n in (1,2,6,12):
    arts = page(n)["data"]["catalogs"][0]["articles"]
    def d(ms): return datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"page {n}: {len(arts)} arts, {d(arts[-1]['releaseDate'])} .. {d(arts[0]['releaseDate'])}")
    for x in arts[:3]:
        print("   ", d(x['releaseDate']), "|", x['title'][:95])
