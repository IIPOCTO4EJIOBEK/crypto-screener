#!/usr/bin/env python
"""Compact summary of /tmp/flow/results.json.
PRIMARY = NON-OVERLAPPING windows (every h-th minute).
Overlapping values are printed as reference only.
"""
import json
import numpy as np

R = json.load(open("/tmp/flow/results.json"))
FEATURES = ["signed_base", "signed_usdt", "imb_base", "imb_usdt", "n_trades", "avg_size"]
HORIZONS = [1, 5, 15, 30]
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
SYSHORT = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "DOGEUSDT": "DOGE"}


def cell(scope, feat, h, key="nonoverlap"):
    if scope == "pooled":
        return R["pooled"].get(f"{feat}|{h}", {}).get(key, {})
    return R["primary"].get(scope, {}).get(f"{feat}|{h}", {}).get(key, {})


def matrix(title, field, fmt, key="nonoverlap"):
    print()
    print("=" * 96)
    print(title)
    print("=" * 96)
    print(f"{'feature':<13}{'h':>3} |" + "".join(f"{SYSHORT[s]:>11}" for s in SYMS) + f"{'POOLED':>11}")
    print("-" * 96)
    for f in FEATURES:
        for h in HORIZONS:
            row = f"{f:<13}{h:>3} |"
            for s in SYMS + ["pooled"]:
                v = cell(s, f, h, key).get(field, float("nan"))
                try:
                    row += f"{fmt.format(v):>11}"
                except (ValueError, TypeError):
                    row += f"{'--':>11}"
            print(row)
        print("-" * 96)


matrix("TABLE A: NON-OVERLAPPING Pearson(feature_t, fwd ret t..t+h)  [PRIMARY]",
       "pearson", "{:+.4f}")
matrix("TABLE B: NON-OVERLAPPING Spearman  [PRIMARY]", "spearman", "{:+.4f}")
matrix("TABLE C: NON-OVERLAPPING sign-match share (sign(feat_t)==sign(fwd ret))",
       "sign_match", "{:.4f}")
matrix("TABLE D: non-overlapping n (observations)", "n", "{:.0f}")

print()
print("=" * 96)
print("REFERENCE ONLY - OVERLAPPING Pearson (dependent observations, n_eff ~ n/h)")
print("=" * 96)
matrix("TABLE E: overlapping Pearson", "pearson", "{:+.4f}", key="full")

print()
print("=" * 96)
print("MAIN QUESTION: signed flow -> forward return. sign & magnitude (NON-OVERLAPPING)")
print("  negatives => flow sign ANTI-predicts (mean-reversion)")
print("=" * 96)
for f in ["signed_base", "signed_usdt", "imb_base", "imb_usdt"]:
    print(f"\n{f}   (cell = non-overlap Pearson / n)")
    print(f"  {'h':>3} |" + "".join(f"{SYSHORT[s]:>16}" for s in SYMS) + f"{'POOLED':>16}")
    for h in HORIZONS:
        row = f"  {h:>3} |"
        for s in SYMS + ["pooled"]:
            c = cell(s, f, h)
            row += f"  {c.get('pearson',float('nan')):+.4f}/{c.get('n',0):>5}"
        print(row)

print()
print("=" * 96)
print("CONTROLS (pooled)")
print("=" * 96)
print(f"{'feature':<13}{'h':>3} | {'ACTUAL P(no)':>13} | {'CS_A P(no)':>11} | "
      f"{'CS_B P mean':>12}{'sd':>8} | {'ACT sign(no)':>13} | {'CS_B sign':>10}{'sd':>7}")
for f in FEATURES:
    for h in HORIZONS:
        act = cell("pooled", f, h)
        ca = R["ctrlA"].get(f"{f}|{h}", {}).get("nonoverlap", {})
        cb = R["ctrlB"].get(f"{f}|{h}", {})
        print(f"{f:<13}{h:>3} | {act.get('pearson',float('nan')):>+13.4f} | "
              f"{ca.get('pearson',float('nan')):>+11.4f} | "
              f"{cb.get('pearson_mean',float('nan')):>+12.4f}{cb.get('pearson_sd',float('nan')):>8.4f} | "
              f"{act.get('sign_match',float('nan')):>13.4f} | "
              f"{cb.get('sign_mean',float('nan')):>10.4f}{cb.get('sign_sd',float('nan')):>7.4f}")

print()
print("=" * 96)
print("STABILITY: first 10 days vs last 10 days (Pearson, per symbol)")
print("=" * 96)
for f in ["signed_base", "imb_base", "n_trades", "avg_size"]:
    for h in HORIZONS:
        row = R["stability"].get(f"{f}|{h}", {})
        if not row:
            continue
        parts = []
        for s in SYMS:
            a, b = row.get(f"{s}|first", {}), row.get(f"{s}|last", {})
            parts.append(f"{SYSHORT[s]} {a.get('pearson',float('nan')):+.3f}/{b.get('pearson',float('nan')):+.3f}")
        print(f"  {f:<12} h={h:<3} " + "  ".join(parts))
    print()
