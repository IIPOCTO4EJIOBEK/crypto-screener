#!/usr/bin/env python
"""Stage B: predictive power of microstructure features on forward returns.

Features (per 1m bar): signed flow (base & usdt), trade imbalance (base & usdt),
n_trades, avg_trade_size.
Targets: forward return over h in {1,5,15,30} min, from 1m closes.

Honesty rules baked in:
  * full-sample Pearson/Spearman are computed on OVERLAPPING windows -> we also
    report a NON-OVERLAPPING subsample (every h-th minute) which is clean.
  * no p-values are presented as proof for overlapping samples; we report n_eff=n/h.
  * controls: (a) feature shifted by 1440 min (prev day, same minute-of-day);
              (b) within-day random permutation of the feature, 20 draws.
  * stability: first 10 days vs last 10 days.
Outputs: /tmp/flow/results.txt (human), /tmp/flow/results.json (machine)
"""
import os, json, glob, re
import numpy as np
import pandas as pd

ROOT = "/mnt/data/binance-archive/daily"
MIN = "/tmp/flow/minutes"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
HORIZONS = [1, 5, 15, 30]
FEATURES = ["signed_base", "signed_usdt", "imb_base", "imb_usdt", "n_trades", "avg_size"]
LOG = open("/tmp/flow/stage_b.log", "w")
OUT = open("/tmp/flow/results.txt", "w")


def log(m=""):
    print(m, flush=True)
    OUT.write(str(m) + "\n")
    OUT.flush()
    LOG.write(str(m) + "\n")
    LOG.flush()


def file_day(path):
    return re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path)).group(1)


def load_klines(sym):
    fs = sorted(glob.glob(f"{ROOT}/klines/{sym}/1m/{sym}-1m-*.zip"))
    parts = []
    for f in fs:
        try:
            df = pd.read_csv(f, usecols=["open_time", "close", "volume", "count",
                                         "taker_buy_volume"],
                             dtype={"open_time": "int64", "close": "float64",
                                    "volume": "float64", "count": "int64",
                                    "taker_buy_volume": "float64"})
        except Exception as e:
            log(f"  klines ERR {os.path.basename(f)}: {e}")
            continue
        df = df.rename(columns={"open_time": "minute"})
        df["day"] = file_day(f)
        parts.append(df)
    kl = pd.concat(parts, ignore_index=True)
    kl["day"] = kl["day"].astype(str)
    return kl


def build(sym):
    agg = pd.read_csv(f"{MIN}/{sym}.csv", dtype={"day": str})
    kl = load_klines(sym)
    # keep only days present in BOTH (aggTrades downloaded + klines cached)
    days = sorted(set(agg["day"]) & set(kl["day"]))
    agg = agg[agg["day"].isin(days)]
    kl = kl[kl["day"].isin(days)]
    df = agg.merge(kl, on=["minute", "day"], how="inner", suffixes=("", "_kl"))

    df["imb_base"] = df["signed_base"] / df["total_base"]
    df["imb_usdt"] = df["signed_usdt"] / df["total_usdt"]
    df["avg_size"] = df["total_base"] / df["n_trades"]

    # data-quality cross-check: our taker-buy base volume vs kline taker_buy_volume
    qc = np.corrcoef(df["buy_base"], df["taker_buy_volume"])[0, 1]
    ratio = (df["buy_base"] / df["taker_buy_volume"]).median()

    # build a complete minute grid so holes are explicit NaNs, then fwd returns
    df = df.sort_values("minute").reset_index(drop=True)
    grid = np.arange(df["minute"].iloc[0], df["minute"].iloc[-1] + 60000, 60000)
    df = df.set_index("minute").reindex(grid)
    df.index.name = "minute"
    df["symbol"] = sym
    for h in HORIZONS:
        df[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1.0
    df["day"] = df["day"].ffill().bfill()
    return df, days, qc, ratio


def metrics(feat, ret):
    """Return dict of metrics for one (feature, return) pair. Overlap-aware."""
    m = np.isfinite(feat) & np.isfinite(ret)
    f, r = feat[m], ret[m]
    n = len(f)
    if n < 30:
        return {"n": n, "pearson": np.nan, "spearman": np.nan, "sign_match": np.nan,
                "sign_n": 0, "n_eff": 0}
    pear = float(np.corrcoef(f, r)[0, 1]) if f.std() > 0 and r.std() > 0 else np.nan
    # rank correlation
    fr = pd.Series(f).rank().to_numpy()
    rr = pd.Series(r).rank().to_numpy()
    spear = float(np.corrcoef(fr, rr)[0, 1]) if fr.std() > 0 and rr.std() > 0 else np.nan
    sf, sr = np.sign(f), np.sign(r)
    nz = (sf != 0) & (sr != 0)
    sm = float((sf[nz] == sr[nz]).mean()) if nz.sum() > 0 else np.nan
    return {"n": n, "pearson": pear, "spearman": spear, "sign_match": sm,
            "sign_n": int(nz.sum()), "n_eff": n}


def nonoverlap_metrics(feat, ret, h):
    """Clean subsample: take every h-th minute so windows never overlap."""
    m = np.isfinite(feat) & np.isfinite(ret)
    idx = np.flatnonzero(m)[::h]
    if len(idx) < 30:
        return {"n": 0, "pearson": np.nan, "spearman": np.nan, "sign_match": np.nan,
                "sign_n": 0, "n_eff": 0}
    return metrics(feat[idx], ret[idx])


def fmt_row(label, mt, mo, h):
    return (f"  {label:<16} n={mt['n']:>6} n_eff~{mt['n']//h:>6} "
            f"P={mt['pearson']:+.4f} S={mt['spearman']:+.4f} "
            f"sign={mt['sign_match']:.4f} (n={mt['sign_n']})\n"
            f"  {'  [nonoverlap]':<16} n={mo['n']:>6} "
            f"P={mo['pearson']:+.4f} S={mo['spearman']:+.4f} "
            f"sign={mo['sign_match']:.4f} (n={mo['sign_n']})")


def main():
    panels, daymap = {}, {}
    log("=" * 78)
    log("STAGE B: microstructure -> forward return predictive power")
    log("=" * 78)
    log("")
    log("--- data loaded ---")
    for sym in SYMS:
        if not os.path.exists(f"{MIN}/{sym}.csv"):
            log(f"{sym}: NO aggTrades aggregate, skipped")
            continue
        df, days, qc, ratio = build(sym)
        panels[sym] = df
        daymap[sym] = days
        log(f"{sym}: days={len(days)} ({days[0]}..{days[-1]}) minutes={df['close'].notna().sum()}"
            f" qc_corr(buy_base,klines taker_buy)= {qc:.4f} median_ratio={ratio:.4f}")

    # restrict every symbol to the day set common to ALL symbols, so the
    # per-symbol / pooled / stability comparisons are on the same sample.
    if len(panels) > 1:
        common = sorted(set.intersection(*[set(v) for v in daymap.values()]))
        if len(common) >= 6:
            log("")
            log(f"common days across all symbols: {len(common)} ({common[0]}..{common[-1]})")
            for sym in list(panels):
                df = panels[sym]
                panels[sym] = df[df["day"].isin(common)]
                daymap[sym] = common
        else:
            log(f"common day set only {len(common)} days -> using per-symbol available days")

    # ---------------- primary table: per symbol per horizon, feature x horizon
    log("")
    log("=" * 78)
    log("PRIMARY: per-symbol, feature x horizon")
    log("  P=Pearson(overlapping)  S=Spearman(overlapping)  sign=share of matching signs")
    log("  [nonoverlap]= every h-th minute (clean)   NOTE: hoisted P/S are overlapping ->")
    log("  observations are dependent; n_eff ~ n/h. No p-values claimed as proof.")
    log("=" * 78)
    primary = {}
    for sym in [s for s in SYMS if s in panels]:
        df = panels[sym]
        log(f"\n### {sym}  days={len(daymap[sym])}")
        primary[sym] = {}
        for feat in FEATURES:
            if feat not in df.columns:
                continue
            for h in HORIZONS:
                mt = metrics(df[feat].to_numpy(), df[f"fwd{h}"].to_numpy())
                mo = nonoverlap_metrics(df[feat].to_numpy(), df[f"fwd{h}"].to_numpy(), h)
                primary[sym][f"{feat}|{h}"] = {"full": mt, "nonoverlap": mo}
                log(f" {feat} -> fwd{h}m")
                log(fmt_row("overlapping", mt, mo, h))

    # ---------------- pooled (within-symbol standardized)
    log("")
    log("=" * 78)
    log("POOLED (features & returns z-scored WITHIN symbol, then concatenated)")
    log("=" * 78)
    pooled = {}
    for feat in FEATURES:
        for h in HORIZONS:
            F, R, HW = [], [], []
            for sym in [s for s in SYMS if s in panels]:
                df = panels[sym]
                f = df[feat].to_numpy(float)
                r = df[f"fwd{h}"].to_numpy(float)
                m = np.isfinite(f) & np.isfinite(r)
                if m.sum() < 100:
                    continue
                fz = (f[m] - np.nanmean(f[m])) / np.nanstd(f[m])
                rz = (r[m] - np.nanmean(r[m])) / np.nanstd(r[m])
                F.append(fz); R.append(rz); HW.append(np.isfinite(f))
            if not F:
                continue
            F = np.concatenate(F); R = np.concatenate(R)
            mt = metrics(F, R)
            mo = metrics(F[::h], R[::h])
            pooled[f"{feat}|{h}"] = {"full": mt, "nonoverlap": mo}
            log(f" {feat} -> fwd{h}m")
            log(fmt_row("pooled", mt, mo, h))

    # ---------------- control A: shift feature by 1440 min (prev day, same minute)
    log("")
    log("=" * 78)
    log("CONTROL A: feature shifted +1440 min (previous day, same minute-of-day)")
    log("=" * 78)
    ctrlA = {}
    for feat in FEATURES:
        for h in HORIZONS:
            F, R = [], []
            for sym in [s for s in SYMS if s in panels]:
                df = panels[sym]
                f = df[feat].shift(1440).to_numpy(float)
                r = df[f"fwd{h}"].to_numpy(float)
                m = np.isfinite(f) & np.isfinite(r)
                if m.sum() < 100:
                    continue
                F.append((f[m] - np.nanmean(f[m])) / np.nanstd(f[m]))
                R.append((r[m] - np.nanmean(r[m])) / np.nanstd(r[m]))
            if not F:
                continue
            F = np.concatenate(F); R = np.concatenate(R)
            mt = metrics(F, R); mo = metrics(F[::h], R[::h])
            ctrlA[f"{feat}|{h}"] = {"full": mt, "nonoverlap": mo}
            log(f" {feat} -> fwd{h}m")
            log(fmt_row("shift1440", mt, mo, h))

    # ---------------- control B: within-day random permutation, 20 draws
    log("")
    log("=" * 78)
    log("CONTROL B: within-day random permutation of feature (20 draws) -> mean/sd")
    log("=" * 78)
    rng = np.random.default_rng(20260929)
    ctrlB = {}
    for feat in FEATURES:
        for h in HORIZONS:
            pears, sms = [], []
            for draw in range(20):
                F, R = [], []
                for sym in [s for s in SYMS if s in panels]:
                    df = panels[sym]
                    f = df[feat].to_numpy(float).copy()
                    r = df[f"fwd{h}"].to_numpy(float)
                    day = df["day"].to_numpy()
                    # permute feature values within each day
                    for d in np.unique(day):
                        sel = np.flatnonzero(day == d)
                        vals = f[sel]
                        ok = np.isfinite(vals)
                        if ok.sum() > 2:
                            vals[ok] = rng.permutation(vals[ok])
                            f[sel] = vals
                    m = np.isfinite(f) & np.isfinite(r)
                    if m.sum() < 100:
                        continue
                    F.append((f[m] - np.nanmean(f[m])) / np.nanstd(f[m]))
                    R.append((r[m] - np.nanmean(r[m])) / np.nanstd(r[m]))
                if not F:
                    continue
                F = np.concatenate(F); R = np.concatenate(R)
                pears.append(np.corrcoef(F, R)[0, 1])
                sf, sr = np.sign(F), np.sign(R)
                nz = (sf != 0) & (sr != 0)
                sms.append((sf[nz] == sr[nz]).mean())
            ctrlB[f"{feat}|{h}"] = {
                "pearson_mean": float(np.mean(pears)), "pearson_sd": float(np.std(pears)),
                "sign_mean": float(np.mean(sms)), "sign_sd": float(np.std(sms))}
            log(f" {feat:<12} -> fwd{h:<2}m  rand P={np.mean(pears):+.4f}+-{np.std(pears):.4f}"
                f"  rand sign={np.mean(sms):.4f}+-{np.std(sms):.4f}")

    # ---------------- stability: first vs last half of days
    log("")
    log("=" * 78)
    log("STABILITY: first half of days vs second half (pooled, per symbol too)")
    log("=" * 78)
    stab = {}
    for feat in ["signed_base", "imb_base", "n_trades", "avg_size"]:
        for h in HORIZONS:
            row = {}
            for sym in [s for s in SYMS if s in panels]:
                df = panels[sym]
                ds = sorted(daymap[sym])
                half = len(ds) // 2
                h1, h2 = set(ds[:half]), set(ds[half:])
                for nm, dm in (("first", h1), ("last", h2)):
                    sel = df["day"].isin(dm).to_numpy()
                    f = df.loc[sel, feat].to_numpy(float)
                    r = df.loc[sel, f"fwd{h}"].to_numpy(float)
                    mt = metrics(f, r)
                    row[f"{sym}|{nm}"] = mt
            stab[f"{feat}|{h}"] = row
            log(f"\n {feat} -> fwd{h}m")
            for sym in [s for s in SYMS if s in panels]:
                a = row.get(f"{sym}|first", {}); b = row.get(f"{sym}|last", {})
                log(f"   {sym:<9} first P={a.get('pearson', float('nan')):+.4f} "
                    f"sign={a.get('sign_match', float('nan')):.4f} n={a.get('n',0):>6} | "
                    f"last P={b.get('pearson', float('nan')):+.4f} "
                    f"sign={b.get('sign_match', float('nan')):.4f} n={b.get('n',0):>6}")

    json.dump({"primary": primary, "pooled": pooled, "ctrlA": ctrlA,
               "ctrlB": ctrlB, "stability": stab,
               "days": {s: daymap[s] for s in daymap}},
              open("/tmp/flow/results.json", "w"), indent=1, default=str)
    log("\nSTAGE_B_DONE -> /tmp/flow/results.json")


if __name__ == "__main__":
    main()
