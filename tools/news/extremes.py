import json, sys
from datetime import datetime, timezone
D = json.load(open("listings_measured.json"))
S = [c for c in D["coins"] if not c.get("reason") and not c["data"]["1h"].get("pre_existing")]
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
w = lambda c,k: c["data"]["1h"]["windows"][k]
print("=== топ-12 по max_ret 72h ===")
for c in sorted(S, key=lambda c:-w(c,"72h")["max_ret"])[:12]:
    d=c["data"]["1h"]
    print(f"  {c['ticker']:11} {f(c['announce_ms'])}  base={d['base']:.8g}  max72 {w(c,'72h')['max_ret']:+9.1%}  close72 {w(c,'72h')['close_ret']:+9.1%}  min72 {w(c,'72h')['min_ret']:+8.1%}")
print()
print("=== топ-12 по min_ret (глубина падения) ===")
for c in sorted(S, key=lambda c:w(c,"72h")["min_ret"])[:12]:
    d=c["data"]["1h"]
    print(f"  {c['ticker']:11} {f(c['announce_ms'])}  base={d['base']:.8g}  min72 {w(c,'72h')['min_ret']:+8.1%}  max72 {w(c,'72h')['max_ret']:+9.1%}  close72 {w(c,'72h')['close_ret']:+9.1%}")
print()
print("=== min_ret 1h == -100% (подозрение на данные) ===")
bad=[c for c in S if w(c,"1h")["min_ret"] <= -0.999]
for c in bad: print("  ", c["ticker"], f(c["announce_ms"]), c["data"]["1h"]["base"], w(c,"1h"))
print()
print("=== худшие close_ret 72h ===")
for c in sorted(S, key=lambda c:w(c,"72h")["close_ret"])[:12]:
    print(f"  {c['ticker']:11} {f(c['announce_ms'])}  close72 {w(c,'72h')['close_ret']:+9.1%}  max72 {w(c,'72h')['max_ret']:+9.1%}")
