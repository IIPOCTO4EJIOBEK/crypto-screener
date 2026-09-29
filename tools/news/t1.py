import sys, json, time
sys.path.insert(0, "../..")
from src.data import market
r = market._get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                {"type":1,"pageNo":1,"pageSize":50,"catalogId":48}, use_proxy=True)
print(type(r), list(r.keys())[:10] if isinstance(r,dict) else "")
d = r.get("data", r)
print("data keys:", list(d.keys()) if isinstance(d,dict) else type(d))
cat = d.get("catalogs")
print("catalogs:", json.dumps(cat, ensure_ascii=False)[:300] if cat else None)
arts = d.get("articles")
if arts:
    print("n articles:", len(arts))
    print(json.dumps(arts[0], ensure_ascii=False)[:800])
    print("total:", d.get("total"))
