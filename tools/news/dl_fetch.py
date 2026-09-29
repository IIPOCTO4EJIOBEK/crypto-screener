import sys, json, re, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "../..")
from src.data import market
A = json.load(open("announcements.json"))["161"]
URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"

def body(code):
    for a in range(3):
        try:
            d = market._get(URL, {"articleCode": code}, use_proxy=True)
            return d
        except Exception as e:
            time.sleep(0.5*(a+1))
            err = str(e)[:100]
    return {"__err": err}

def texts(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "text" and isinstance(v, str): out.append(v)
            else: texts(v, out)
    elif isinstance(o, list):
        for v in o: texts(v, out)

def run(x):
    d = body(x["code"])
    out = {"id": x["id"], "title": x["title"], "release_ms": x["releaseDate"], "code": x["code"]}
    if "__err" in d:
        out["err"] = d["__err"]; return out
    t = []
    texts(d, t)
    s = "\n".join(t) + "\n" + json.dumps(d, ensure_ascii=False)
    out["pairs"] = sorted(set(f"{a}/{b}" for a, b in
        re.findall(r"\b([A-Z0-9]{2,15})/(USDT|USDC|BTC|ETH|BNB|FDUSD|TRY|BUSD|EUR|BRL|TUSD|DAI|XRP|SOL|DOGE)\b", s)))
    out["bases"] = sorted(set(p.split("/")[0] for p in out["pairs"]))
    # эффективное время делистинга из текста
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \(UTC\)", s)
    out["effective_utc"] = m.group(1) if m else None
    return out

res = []
with ThreadPoolExecutor(max_workers=4) as ex:
    for i, r in enumerate(ex.map(run, A)):
        res.append(r)
        if (i+1) % 50 == 0: print(f"  {i+1}/{len(A)}", flush=True)
json.dump(res, open("delist_bodies.json","w"), ensure_ascii=False)
err = sum(1 for r in res if r.get("err"))
nopairs = sum(1 for r in res if not r.get("err") and not r.get("pairs"))
nb = sum(len(r.get("bases",[])) for r in res)
print(f"тел получено {len(res)-err}, ошибок {err}, без пар {nopairs}, всего базовых тикеров {nb}")
