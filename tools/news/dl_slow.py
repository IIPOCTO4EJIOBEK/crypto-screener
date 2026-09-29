"""Терпеливая выгрузка тел анонсов о делистинге: ждёт снятия 429, затем по 1 запросу
раз в ~2 с, с сохранением после каждого (возобновляемо)."""
import sys, json, re, time, os, signal
sys.path.insert(0, "../..")
import requests
A = json.load(open("announcements.json"))["161"]
OUT = "delist_raw.json"
raw = json.load(open(OUT)) if os.path.exists(OUT) else {}
URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
s = requests.Session(); s.trust_env = True
s.headers["User-Agent"] = "crypto-screener/0.1 (research)"
def probe():
    try:
        r = s.get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                  params={"type":1,"pageNo":1,"pageSize":50,"catalogId":48}, timeout=30)
        return r.status_code
    except Exception:
        return -1
# 1) ждём снятия 429
for i in range(60):
    c = probe()
    print(f"[{time.strftime('%H:%M:%S')}] проба: HTTP {c}", flush=True)
    if c == 200: break
    time.sleep(30)
else:
    print("429 не снялся за 30 мин — выход", flush=True); sys.exit(3)
# 2) качаем тела
fails = 0
for i, x in enumerate(A):
    if x["code"] in raw: continue
    try:
        r = s.get(URL, params={"articleCode": x["code"]}, timeout=30)
        if r.status_code == 429:
            fails += 1
            print(f"  429 на {i} ({x['code'][:10]}), пауза 60 с", flush=True)
            json.dump(raw, open(OUT,"w"), ensure_ascii=False)
            time.sleep(60)
            r = s.get(URL, params={"articleCode": x["code"]}, timeout=30)
        if r.status_code == 200:
            raw[x["code"]] = r.json()
        else:
            raw[x["code"]] = None
    except Exception as e:
        raw[x["code"]] = None
    if (i+1) % 25 == 0:
        json.dump(raw, open(OUT,"w"), ensure_ascii=False)
        print(f"  {i+1}/{len(A)}  (429: {fails})", flush=True)
    time.sleep(2.0)
json.dump(raw, open(OUT,"w"), ensure_ascii=False)
print(f"готово: сохранено {len(raw)} тел, 429 {fails}", flush=True)
