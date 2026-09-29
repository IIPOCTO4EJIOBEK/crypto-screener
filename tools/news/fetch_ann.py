import sys, json, time
from datetime import datetime, timezone
sys.path.insert(0, "../..")
from src.data import market

URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"

def page(n, cat, size=50):
    return market._get(URL, {"type":1,"pageNo":n,"pageSize":size,"catalogId":cat}, use_proxy=True)

def grab(cat, pages):
    out, seen = [], set()
    for n in range(1, pages+1):
        for attempt in range(3):
            try:
                arts = page(n, cat)["data"]["catalogs"][0]["articles"]
                break
            except Exception as e:
                print(f"  cat{cat} p{n} err {type(e).__name__} {str(e)[:70]}", file=sys.stderr)
                arts = None
                time.sleep(1.5)
        if not arts:
            print(f"  cat{cat} p{n}: FAILED, stop", file=sys.stderr); break
        for a in arts:
            if a["id"] not in seen:
                seen.add(a["id"]); out.append(a)
        time.sleep(0.25)
    return out

res = {}
for cat, pages in ((48, 24), (161, 12)):
    arts = grab(cat, pages)
    res[str(cat)] = arts
    if arts:
        f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"catalog {cat}: {len(arts)} записей, {f(arts[-1]['releaseDate'])} .. {f(arts[0]['releaseDate'])}")
    else:
        print(f"catalog {cat}: 0 записей")

json.dump(res, open("announcements.json","w"), ensure_ascii=False, indent=1)
print("saved announcements.json")
