"""Замер реакции цены на анонсы листингов Binance (catalogId=48).
Только чтение: свечи через src/data/market.py. Результат — listings_measured.json
"""
import json, re, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
sys.path.insert(0, "../..")
from src.data import market

d = json.load(open("catalog48_all.json"))
PAREN = re.compile(r"\(([^()]{1,20})\)")

def ok(tk):
    tk = tk.strip()
    if not tk or tk.isdigit(): return None
    if re.fullmatch(r"\d{4}(-\d{2}-\d{2})?", tk): return None
    if not re.fullmatch(r"[A-Za-z0-9]{1,15}", tk): return None
    if len(tk) > 1 and tk == tk.lower(): return None   # слово-ложняк ("old")
    return tk.upper()

# --- Tier B: записи без скобок, тикер виден текстом (разобраны вручную по шаблону) ---
TIERB = {
 ":CommunityCoinVote:": None,  # заглушка
}
WINNER = re.compile(r"Winner\s+([A-Z0-9]{2,10})\s+and Has Distributed")
BSC = re.compile(r"BSC Projects,\s+([A-Z0-9]{2,10})\s+and\s+([A-Z0-9]{2,10}),")
TAIL = {"Binance Will List GYEN":"GYEN", "Binance Will List BFUSD and Introduce BFUSD Zero Trading Fee Promotion":"BFUSD",
        "Binance Will List KGST & Enable Trading Bots Services on Binance Spot - 2025-12-24":"KGST",
        "Binance Will List ZRX Market":"ZRX", "Binance Will List OMG Market":"OMG",
        "Binance Opens New BUSD Fiat Gateway and Will List BUSD":"BUSD"}
PAX = re.compile(r"Binance Will List PAX/(USDT|BTC|BNB|ETH) Trading Pair")

coins, dropped, tierb = [], [], []
for x in d:
    t, ms, aid = x["title"], x["releaseDate"], x["id"]
    if "Will List" not in t: continue
    if any(s in t for s in ("Leveraged Token","Options")) or t.startswith(("Binance Futures","Binance Margin")):
        continue
    toks = [ok(tk) for tk in PAREN.findall(t)]
    toks = [tk for tk in toks if tk]
    if toks:
        for tk in toks: coins.append({"ticker":tk,"announce_ms":ms,"tier":"A","title":t,"id":aid})
        continue
    m = WINNER.search(t)
    if m: tierb.append((m.group(1), ms)); coins.append({"ticker":m.group(1),"announce_ms":ms,"tier":"B","title":t,"id":aid}); continue
    m = BSC.search(t)
    if m:
        for tk in m.groups(): coins.append({"ticker":tk,"announce_ms":ms,"tier":"B","title":t,"id":aid}); tierb.append((tk,ms))
        continue
    m = PAX.search(t); 
    if m: tierb.append(("PAX",ms)); coins.append({"ticker":"PAX","announce_ms":ms,"tier":"B","title":t,"id":aid}); continue
    if t in TAIL:
        tk = TAIL[t]; tierb.append((tk,ms)); coins.append({"ticker":tk,"announce_ms":ms,"tier":"B","title":t,"id":aid}); continue
    dropped.append({"title":t,"announce_ms":ms,"id":aid})

# дедуп PAX (4 записи) — берём самую раннюю
seen, uniq = set(), []
for c in sorted(coins, key=lambda z: z["announce_ms"]):
    k = c["ticker"]
    if k in seen: continue
    seen.add(k); uniq.append(c)
print(f"распознано записей: {len(coins)} (уникальных монет {len(uniq)}); "
      f"из них Tier B {sum(1 for c in uniq if c['tier']=='B')}; отброшено записей {len(dropped)}", flush=True)

# --- какие пары есть на Binance ---
info = market._get("https://data-api.binance.vision/api/v3/exchangeInfo", None, use_proxy=True)
SYM = {s["symbol"]: s["status"] for s in info["symbols"]}
print("exchangeInfo: символов", len(SYM), flush=True)

H1, M15 = 3600_000, 900_000
def fetch(sym, tf, end_ms):
    return market.ohlcv("binance", sym, tf, 1000, end_ms=end_ms)

def series(sym, tf, end_ms):
    for a in range(3):
        try: return fetch(sym, tf, end_ms)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(0.6*(a+1))
    return err

def fetch_pair(sym, am):
    """1h-серия вокруг анонса; если пары тогда не было — ищем, когда появилась."""
    s1h = series(sym, "1h", am + 13*86400_000)
    late = None
    if isinstance(s1h, str) or not s1h:
        late = None
        for d in (30, 90, 180, 365, 730, 1460):
            s2 = series(sym, "1h", am + d*86400_000)
            if s2 and not isinstance(s2, str):
                late = (d, s2[0].ts); break
    s15 = series(sym, "15m", am + 9*86400_000)
    return s1h, s15, late

def run(c):
    tk, am = c["ticker"], c["announce_ms"]
    sym = tk + "USDT"
    out = dict(c); out["symbol"] = sym
    if sym not in SYM:
        out["reason"] = "нет пары " + sym + " на Binance"
        return out
    out["status"] = SYM[sym]
    s1h, s15, late = fetch_pair(sym, am)
    if late:
        out["reason"] = ("пара %s появилась только примерно через %d дн. после анонса "
                         "(первая свеча %s)" % (sym, late[0], fmt(late[1])))
        out["pair_late_days"] = late[0]; out["pair_first_ts"] = late[1]
        return out
    if isinstance(s1h, str):
        out["reason"] = "свечи 1h не получены: " + s1h
        return out
    if not s1h:
        out["reason"] = "пустой ответ по свечам"
        return out
    res = {}
    for tf, s, wins, iv in (("15m", s15, [1,4,24,72], 900_000),
                            ("1h", s1h, [1,4,24,72,120], 3_600_000)):
        if isinstance(s, str):
            res[tf] = {"error": s}; continue
        if not s:
            res[tf] = {"error": "пустой ответ"}; continue
        t0 = s[0].ts
        pre = (am - t0) > 2*86400_000
        base = s[0].open
        last = s[-1].ts
        w = {}
        for h in wins:
            end = t0 + h*3_600_000
            win = [c2 for c2 in s if t0 <= c2.ts < end]
            if not win: w[f"{h}h"] = None; continue
            w[f"{h}h"] = {
                "n_candles": len(win),
                "max_ret": max(c2.high for c2 in win)/base - 1,
                "min_ret": min(c2.low  for c2 in win)/base - 1,
                "close_ret": win[-1].close/base - 1,
                "complete": last >= end - iv,
            }
        res[tf] = {"t0": t0, "lag_h": (t0-am)/3_600_000, "base": base,
                   "pre_existing": pre, "n_candles": len(s),
                   "span_h": (last-t0)/3_600_000, "windows": w,
                   "first_qvol": s[0].quote_volume}
    out["data"] = res
    return out

def fmt(ms): return datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

results, done = [], [0]
lock = threading.Lock()
with ThreadPoolExecutor(max_workers=4) as ex:
    for r in ex.map(run, uniq):
        with lock:
            results.append(r); done[0]+=1
            if done[0] % 25 == 0:
                print(f"  {done[0]}/{len(uniq)}", flush=True)

json.dump({"coins": results, "dropped": dropped}, open("listings_measured.json","w"), ensure_ascii=False)
nf = sum(1 for r in results if r.get("reason"))
print(f"готово: {len(results)} монет, без пары на Binance {nf}", flush=True)
