"""Замер делистингов (catalogId=161) по заголовкам анонсов.

Тела анонсов семейства "Notice of Removal of Spot Trading Pairs" не достались
(HTTP 429 на www.binance.com/bapi), поэтому берём только те записи, где тикер
виден прямо в заголовке. Отброшенные считаем по семействам.

Метрики: утечка ДО анонса (-72h/-24h/-4h) и реакция ПОСЛЕ (+1h/+4h/+24h/+72h).
"""
import json, re, sys, time, statistics as st
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "../..")
from src.data.market import ohlcv

D = json.load(open("announcements.json"))["161"]
print("записей в catalogId=161:", len(D))

STOP = set("""Spot Trading Pairs Pair Contract Contracts Multiple Margin Futures
Isolated Cross Loans Loan the and Leverage Tiers Update Upcoming Changes Notice
Removal Delist Delisting Redemption Cease Binance USDⓈM CoinM XM X M Perpetual
Will on of to for and And""".split())

def tokens(seg):
    out = []
    for part in re.split(r",|\s+and\s+|\s*&\s*|\s+", seg):
        t = re.sub(r"\([^)]*\)", " ", part).strip(" .;:")
        t = t.replace("Ⓢ", "").replace("-Margined", "").replace("Margined", "")
        t = re.sub(r"^(USDT|USDC|BUSD|FDUSD|X|COIN|COINM|USDM)$", " ", t)
        t = t.strip(" .;:-")
        if not t or t in STOP or not re.fullmatch(r"[A-Z0-9]{2,12}", t):
            continue
        if re.fullmatch(r"\d+", t):
            continue
        out.append(t)
    return out

records = []
for r in D:
    t = r["title"]
    cat = ("futures" if "Futures" in t else
           "margin" if "Margin" in t else
           "loans" if "Loan" in t else
           "levtoken" if "Leveraged Token" in t else
           "spot")
    tk = []
    if cat == "spot":
        m = re.match(r"Binance Will Delist (.+?) on \d{4}-\d{2}-\d{2}", t)
        if m:
            tk = tokens(m.group(1))
        m2 = re.match(r"Upcoming Changes to (.+?) Spot Trading on Binance", t)
        if m2:
            tk = tokens(m2.group(1))
    elif cat == "futures":
        seg = re.sub(r"^Binance Futures Will Delist( and Update the Leverage & Margin Tiers of)?\s*", "", t)
        seg = re.sub(r"\s*\(?\d{4}-\d{2}-\d{2}\)?\s*$", "", seg)
        seg = re.sub(r"(Perpetual Contracts?|X-Margined Contracts?|Contracts?)", " ", seg)
        tk = tokens(seg)
    records.append({"id": r["id"], "am": r["releaseDate"], "title": t,
                    "cat": cat, "tickers": tk})

by_cat = {}
for r in records:
    c = by_cat.setdefault(r["cat"], {"recs": 0, "with": 0, "without": 0, "lines": []})
    c["recs"] += 1
    if r["tickers"]:
        c["with"] += 1
        c["lines"].append(r)
    else:
        c["without"] += 1

print("\n=== воронка по семействам ===")
for c, v in sorted(by_cat.items(), key=lambda x: -x[1]["recs"]):
    print(f"  {c:9} записей {v['recs']:4}  с тикером в заголовке {v['with']:3}  без тикера {v['without']:3}")

# уникальные тикеры, самая ранняя дата анонса
uniq = {}
for r in by_cat.get("spot", {}).get("lines", []):
    for tk in r["tickers"]:
        if tk not in uniq or r["am"] < uniq[tk]["am"]:
            uniq[tk] = r
spots = sorted(uniq.items(), key=lambda x: x[1]["am"])
print(f"\nспот-делистинги: записей с тикером {by_cat.get('spot',{}).get('with',0)}, "
      f"уникальных тикеров {len(spots)}")
for tk, r in spots:
    print(f"  {tk:10} {time.strftime('%Y-%m-%d', time.gmtime(r['am']/1000))}  {r['title'][:78]}")

fut = {}
for r in by_cat.get("futures", {}).get("lines", []):
    for tk in r["tickers"]:
        if tk not in fut or r["am"] < fut[tk]["am"]:
            fut[tk] = r
print(f"\nфьючерсные делистинги: записей с символом {by_cat.get('futures',{}).get('with',0)}, "
      f"уникальных {len(fut)}")
json.dump({tk: {"am": r["am"], "title": r["title"]} for tk, r in spots},
          open("delist_spot_targets.json", "w"), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- измерение
def at_hour(s, base, h):
    i = base + h
    return s[i] if 0 <= i < len(s) else None


def measure(tk, am, base_sym=None):
    sym = base_sym or (tk + "USDT")
    try:
        s = ohlcv("binance", sym, "1h", 1000, end_ms=am + 96 * 3600_000)
    except Exception as e:
        return {"err": f"{type(e).__name__}: {str(e)[:60]}"}
    if not s:
        return {"err": "пусто"}
    base = None
    for i, c in enumerate(s):
        if c.ts <= am:
            base = i
    if base is None:
        return {"err": "история начинается после анонса"}
    pre = s[base].close
    if pre <= 0:
        return {"err": "нулевая цена"}
    out = {"sym": sym, "n": len(s), "base": base, "pre": pre,
           "lag_h": round((s[base].ts + 3600_000 - am) / 3600_000, 2),
           "end_after_am_h": round((s[-1].ts - am) / 3600_000, 1)}
    for h in (-72, -24, -4):
        c = at_hour(s, base, h)
        if c:
            out[f"pre{h}"] = c.close / pre - 1
    if base >= 72:
        seg = s[base - 72:base + 1]
        p = min(x.low for x in seg if x.low > 0)
        out["pre_min72"] = p / pre - 1
        out["pre_max72"] = max(x.high for x in seg) / pre - 1
    for h in (1, 4, 24, 72):
        c = at_hour(s, base, h)
        if not c:
            out[f"post{h}"] = None
            continue
        out[f"post{h}"] = c.close / pre - 1
        out[f"comp{h}"] = True
        seg = s[base:base + h + 1]
        out[f"max{h}"] = max(x.high for x in seg) / pre - 1
        out[f"min{h}"] = min(x.low for x in seg) / pre - 1
    return out


def run(pairs, label):
    res = {}
    def work(item):
        tk, r = item
        return tk, measure(tk, r["am"])
    with ThreadPoolExecutor(max_workers=2) as ex:
        for tk, m in ex.map(work, pairs):
            res[tk] = m
            time.sleep(0.25)
    ok = {k: v for k, v in res.items() if "err" not in v}
    err = {k: v["err"] for k, v in res.items() if "err" in v}
    print(f"\n=== {label}: измерено {len(ok)} из {len(pairs)}, ошибок {len(err)} ===")
    for k, v in list(err.items())[:40]:
        print(f"  {k:10} {v}")
    json.dump(res, open(f"delist_{label}_measured.json", "w"),
              ensure_ascii=False, indent=1)
    return ok


def stats(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    q = st.quantiles(vals, n=4) if n >= 4 else [vals[0], st.median(vals), vals[-1]]
    k = max(1, int(n * 0.1))
    return {"n": n, "med": st.median(vals), "mean": st.fmean(vals),
            "tmean": st.fmean(vals[k:n - k]) if n > 2 * k else st.fmean(vals),
            "p25": q[0], "p75": q[2], "pos": sum(1 for v in vals if v > 0) / n}


def show(ok, label):
    print(f"\n---------------- {label} (n={len(ok)}) ----------------")
    keys = [("утечка -72ч", "pre-72"), ("утечка -24ч", "pre-24"), ("утечка -4ч", "pre-4"),
            ("закрытие +1ч", "post1"), ("закрытие +4ч", "post4"),
            ("закрытие +24ч", "post24"), ("закрытие +72ч", "post72"),
            ("мин за 72ч до", "pre_min72"), ("макс за 72ч до", "pre_max72"),
            ("макс после +24ч", "max24"), ("мин после +24ч", "min24"),
            ("макс после +72ч", "max72"), ("мин после +72ч", "min72")]
    print(f"{'метрика':18} {'n':>4} {'медиана':>9} {'среднее':>9} {'усеч.ср':>9} "
          f"{'p25':>9} {'p75':>9} {'доля>0':>7}")
    for name, k in keys:
        s = stats([v.get(k) for v in ok.values()])
        if not s:
            print(f"{name:18} {'0':>4}   нет данных")
            continue
        print(f"{name:18} {s['n']:>4} {s['med']*100:>8.1f}% {s['mean']*100:>8.1f}% "
              f"{s['tmean']*100:>8.1f}% {s['p25']*100:>8.1f}% {s['p75']*100:>8.1f}% "
              f"{s['pos']*100:>6.1f}%")
    comp = {h: sum(1 for v in ok.values() if v.get(f"comp{h}")) for h in (1, 4, 24, 72)}
    print("окно дожило до конца:", comp, "из", len(ok))
    for h in (24, 72):
        sub = [v[f"post{h}"] for v in ok.values() if v.get(f"comp{h}")]
        if sub:
            neg = sum(1 for x in sub if x < 0)
            print(f"  на +{h}ч ниже цены перед анонсом: {neg}/{len(sub)} = {neg/len(sub)*100:.1f}%")


spot_ok = run(spots, "spot")
fut_ok = run(sorted(fut.items(), key=lambda x: x[1]["am"]), "futures")
show(spot_ok, "СПОТ-делистинги")
show(fut_ok, "ФЬЮЧЕРС-делистинги")
