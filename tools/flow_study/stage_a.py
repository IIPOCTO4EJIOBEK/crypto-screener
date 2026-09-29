#!/usr/bin/env python
"""Stage A: aggregate aggTrades archives into per-minute microstructure bars.

For each (symbol, day) zip:
  signed_flow_base = sum(q | aggressor BUY) - sum(q | aggressor SELL)   [m=false - m=true]
  signed_flow_usdt = same on q*p
  total_flow_base / total_flow_usdt
  n_trades
Output: /tmp/flow/minutes/{SYMBOL}.csv  (minute epoch ms, all days concatenated)
Raw log: /tmp/flow/stage_a.log
"""
import os, sys, glob, zipfile, time, re
import numpy as np
import pandas as pd

ROOT = "/mnt/data/binance-archive/daily"
OUT = "/tmp/flow/minutes"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
LOG = open("/tmp/flow/stage_a.log", "w")


def log(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    LOG.write(m + "\n")
    LOG.flush()


def minute_bars(path):
    """Aggregate one aggTrades zip -> DataFrame indexed by minute epoch ms."""
    z = zipfile.ZipFile(path)
    name = z.namelist()[0]
    acc = {}
    nrows = 0
    with z.open(name) as fh:
        # read in chunks to bound memory
        reader = pd.read_csv(
            fh,
            usecols=["price", "quantity", "transact_time", "is_buyer_maker"],
            dtype={"price": "float64", "quantity": "float64",
                   "transact_time": "int64", "is_buyer_maker": "bool"},
            chunksize=4_000_000,
        )
        for chunk in reader:
            nrows += len(chunk)
            t = chunk["transact_time"].to_numpy()
            q = chunk["quantity"].to_numpy()
            p = chunk["price"].to_numpy()
            m = chunk["is_buyer_maker"].to_numpy()
            minute = (t // 60000) * 60000
            uniq, inv = np.unique(minute, return_inverse=True)
            agg_buy = ~m  # aggressor bought (buyer is taker)
            qp = q * p
            buy_b = np.bincount(inv, weights=np.where(agg_buy, q, 0.0), minlength=len(uniq))
            sell_b = np.bincount(inv, weights=np.where(m, q, 0.0), minlength=len(uniq))
            buy_u = np.bincount(inv, weights=np.where(agg_buy, qp, 0.0), minlength=len(uniq))
            sell_u = np.bincount(inv, weights=np.where(m, qp, 0.0), minlength=len(uniq))
            ntr = np.bincount(inv, minlength=len(uniq))
            part = pd.DataFrame({
                "minute": uniq, "buy_base": buy_b, "sell_base": sell_b,
                "buy_usdt": buy_u, "sell_usdt": sell_u, "n_trades": ntr,
            })
            acc[len(acc)] = part
    df = pd.concat(acc.values(), ignore_index=True)
    df = df.groupby("minute", as_index=False).sum()
    df["signed_base"] = df["buy_base"] - df["sell_base"]
    df["total_base"] = df["buy_base"] + df["sell_base"]
    df["signed_usdt"] = df["buy_usdt"] - df["sell_usdt"]
    df["total_usdt"] = df["buy_usdt"] + df["sell_usdt"]
    return df, nrows


def file_day(path):
    """Full YYYY-MM-DD from the archive filename (NOT day-of-month)."""
    return re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path)).group(1)


def main():
    os.makedirs(OUT, exist_ok=True)
    for sym in SYMS:
        files = sorted(glob.glob(f"{ROOT}/aggTrades/{sym}/{sym}-aggTrades-*.zip"))
        log(f"[{sym}] {len(files)} aggTrades files")
        parts = []
        for f in files:
            d = file_day(f)
            t0 = time.time()
            try:
                df, nrows = minute_bars(f)
            except Exception as e:
                log(f"  ERR {d}: {type(e).__name__}: {e}")
                continue
            df["day"] = d
            parts.append(df)
            log(f"  {d}: rows={nrows} minutes={len(df)} {time.time()-t0:.1f}s")
        if not parts:
            continue
        out = pd.concat(parts, ignore_index=True).sort_values("minute").reset_index(drop=True)
        out["symbol"] = sym
        out.to_csv(f"{OUT}/{sym}.csv", index=False)
        log(f"[{sym}] wrote {len(out)} minute rows -> {OUT}/{sym}.csv")
    log("STAGE_A_DONE")


if __name__ == "__main__":
    main()
