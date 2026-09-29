#!/usr/bin/env python
"""Phase-robustness of the non-overlapping result.

The 'every h-th minute' subsample picks ONE of h possible phases. If the effect
depends on which minutes get sampled, the non-overlapping number is alignment luck.
Here: for each h, compute non-overlapping Pearson for ALL h phases and report the
spread. Also does the same for the day-shifted control, and for day-halves.

Self-contained (does not import stage_b, to avoid truncating its logs).
Output: /tmp/flow/phase_check.txt
"""
import os, glob, re, json
import numpy as np
import pandas as pd

ROOT = "/mnt/data/binance-archive/daily"
MIN = "/tmp/flow/minutes"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
HORIZONS = [1, 5, 15, 30]
FEATURES = ["signed_base", "signed_usdt", "imb_base", "n_trades"]
OUT = open("/tmp/flow/phase_check.txt", "w")


def log(m=""):
    print(m, flush=True)
    OUT.write(str(m) + "\n")
    OUT.flush()


def file_day(p):
    return re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p)).group(1)


def load(sym):
    agg = pd.read_csv(f"{MIN}/{sym}.csv", dtype={"day": str})
    parts = []
    for f in sorted(glob.glob(f"{ROOT}/klines/{sym}/1m/{sym}-1m-*.zip")):
        try:
            d = pd.read_csv(f, usecols=["open_time", "close"],
                            dtype={"open_time": "int64", "close": "float64"})
        except Exception:
            continue
        d = d.rename(columns={"open_time": "minute"})
        d["day"] = file_day(f)
        parts.append(d)
    kl = pd.concat(parts, ignore_index=True)
    kl["day"] = kl["day"].astype(str)
    days = sorted(set(agg["day"]) & set(kl["day"]))
    agg = agg[agg["day"].isin(days)]
    kl = kl[kl["day"].isin(days)]
    df = agg.merge(kl, on=["minute", "day"], how="inner")
    df["imb_base"] = df["signed_base"] / df["total_base"]
    df = df.sort_values("minute").reset_index(drop=True)
    grid = np.arange(df["minute"].iloc[0], df["minute"].iloc[-1] + 60000, 60000)
    df = df.set_index("minute").reindex(grid)
    for h in HORIZONS:
        df[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1.0
    df["day"] = df["day"].ffill().bfill()
    df["pos"] = np.arange(len(df))
    return df, days


def pear(f, r):
    if len(f) < 30 or f.std() == 0 or r.std() == 0:
        return np.nan
    return float(np.corrcoef(f, r)[0, 1])


def phase_spread(df, feat, h, mask=None):
    """Non-overlapping Pearson for each of the h phases; returns list."""
    f_all = df[feat].to_numpy(float)
    r_all = df[f"fwd{h}"].to_numpy(float)
    pos = df["pos"].to_numpy()
    v = np.isfinite(f_all) & np.isfinite(r_all)
    if mask is not None:
        v &= mask
    out = []
    for p in range(h):
        sel = v & (pos % h == p)
        out.append(pear(f_all[sel], r_all[sel]))
    return out


def main():
    log("=" * 100)
    log("PHASE ROBUSTNESS: non-overlapping Pearson across ALL h phases")
    log("  'spread' = min..max over the h possible phase alignments.")
    log("  If the sign flips across phases, the 'non-overlapping' number is alignment luck.")
    log("=" * 100)
    pooled_rows = []
    for sym in SYMS:
        df, days = load(sym)
        log(f"\n### {sym} days={len(days)} grid={len(df)}")
        for feat in FEATURES:
            for h in HORIZONS:
                vals = np.array(phase_spread(df, feat, h), dtype=float)
                vals = vals[np.isfinite(vals)]
                if len(vals) == 0:
                    continue
                nneg = int((vals < 0).sum())
                log(f"  {feat:<12} h={h:<3} phases n={len(vals)} min={vals.min():+.4f} "
                    f"med={np.median(vals):+.4f} max={vals.max():+.4f} "
                    f"mean={vals.mean():+.4f} sign_neg={nneg}/{len(vals)}")
                pooled_rows.append((sym, feat, h, vals.min(), np.median(vals),
                                    vals.max(), vals.mean(), nneg, len(vals)))

    log("")
    log("=" * 100)
    log("DAY-HALF STABILITY of the non-overlapping estimate (mean over ALL phases)")
    log("  mean_over_phases is more honest than a single phase.")
    log("=" * 100)
    for sym in SYMS:
        df, days = load(sym)
        ds = sorted(days)
        half = len(ds) // 2
        m1 = df["day"].isin(set(ds[:half])).to_numpy()
        m2 = df["day"].isin(set(ds[half:])).to_numpy()
        log(f"\n### {sym}  ({ds[0]}..{ds[half-1]} vs {ds[half]}..{ds[-1]})")
        for feat in ["signed_base", "n_trades"]:
            for h in HORIZONS:
                for nm, mm in (("first", m1), ("last ", m2)):
                    v = np.array(phase_spread(df, feat, h, mm), dtype=float)
                    v = v[np.isfinite(v)]
                    if len(v) == 0:
                        continue
                    log(f"  {feat:<12} h={h:<3} {nm} mean={v.mean():+.4f} "
                        f"med={np.median(v):+.4f} min={v.min():+.4f} max={v.max():+.4f} "
                        f"sign_neg={int((v<0).sum())}/{len(v)}")

    log("")
    log("=" * 100)
    log("PHASE ROBUSTNESS OF THE CONTROL (feature shifted +1440 min)")
    log("=" * 100)
    for sym in SYMS:
        df, days = load(sym)
        df = df.copy()
        for feat in ["signed_base", "n_trades"]:
            df[feat] = df[feat].shift(1440)
            for h in HORIZONS:
                v = np.array(phase_spread(df, feat, h), dtype=float)
                v = v[np.isfinite(v)]
                if len(v) == 0:
                    continue
                log(f"  {sym:<9} {feat:<12} h={h:<3} ctrlA mean={v.mean():+.4f} "
                    f"med={np.median(v):+.4f} min={v.min():+.4f} max={v.max():+.4f}")
            df[feat] = df[feat].copy()  # keep shifted; reloaded next symbol anyway

    json.dump([{"sym": r[0], "feat": r[1], "h": r[2], "min": r[3], "med": r[4],
                "max": r[5], "mean": r[6], "nneg": r[7], "nph": r[8]}
               for r in pooled_rows], open("/tmp/flow/phase_check.json", "w"), indent=1)
    log("\nPHASE_CHECK_DONE")


if __name__ == "__main__":
    main()
