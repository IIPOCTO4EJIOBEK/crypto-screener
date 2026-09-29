"""Разбор тел анонсов о делистинге (delist_raw.json) → списки удаляемых пар.

Тело статьи приходит как JSON-строка внутри ответа CMS, поэтому собираем все
строковые значения рекурсивно (включая вложенную JSON-строку) и ищем в них пары
вида BASE/QUOTE. Пишем цели для замера: отдельно те, где удаляется именно
пара к USDT (чистое событие для XXXUSDT), и отдельно прочие.
"""
import json, re, collections

A = {r["code"]: r for r in json.load(open("announcements.json"))["161"]}
raw = json.load(open("delist_raw.json"))
QUOTES = ("USDT", "USDC", "BTC", "ETH", "BNB", "FDUSD", "TUSD", "BUSD", "DAI",
          "EUR", "TRY", "BRL", "ARS", "JPY", "AEUR", "USD")
PAIR = re.compile(r"\b([A-Z0-9]{2,15})/(%s)\b" % "|".join(QUOTES))


def strings(o, out):
    if isinstance(o, str):
        out.append(o)
        t = o.strip()
        if t[:1] in "[{" and len(t) > 2:          # вложенная JSON-строка
            try:
                strings(json.loads(t), out)
            except Exception:
                pass
    elif isinstance(o, dict):
        for v in o.values():
            strings(v, out)
    elif isinstance(o, list):
        for v in o:
            strings(v, out)
    return out


def title_family(t):
    if "Futures" in t:
        return "futures"
    if "Margin" in t:
        return "margin"
    if "Loan" in t:
        return "loans"
    if "Leveraged Token" in t:
        return "levtoken"
    return "spot"


records = []
for code, body in raw.items():
    a = A.get(code)
    if a is None:
        continue
    fam = title_family(a["title"])
    pairs = set()
    if body:
        for s in strings(body, []):
            for m in PAIR.finditer(s):
                pairs.add((m.group(1), m.group(2)))
    records.append({"code": code, "am": a["releaseDate"], "title": a["title"],
                    "fam": fam, "pairs": sorted(pairs)})

have = [r for r in records if r["pairs"]]
print(f"записей разобрано: {len(records)}; с парами: {len(have)}; без пар: {len(records)-len(have)}")
fam = collections.Counter((r["fam"], bool(r["pairs"])) for r in records)
print("по семьям (семейство, есть пары):", dict(fam))

usdt, other = {}, {}
for r in have:
    for base, quote in r["pairs"]:
        if quote == "USDT":
            if base not in usdt or r["am"] < usdt[base]["am"]:
                usdt[base] = {"am": r["am"], "title": r["title"], "fam": r["fam"],
                              "n_pairs_in_record": len(r["pairs"])}
        else:
            if base not in other or r["am"] < other[base]["am"]:
                other[base] = {"am": r["am"], "title": r["title"], "fam": r["fam"]}

print(f"\nуникальных баз с удалением пары к USDT: {len(usdt)}")
print(f"уникальных баз, удалялись только прочие котировки (USDC/BTC/...): {len(other)}")
by_fam = collections.Counter(v["fam"] for v in usdt.values())
print("из них по семействам:", dict(by_fam))
print("\nпримеры (USDT-пары):")
for k in sorted(usdt, key=lambda x: usdt[x]["am"])[-6:]:
    print(f"  {k:10} {usdt[k]['title'][:72]}")
json.dump(usdt, open("delist_bodies_usdt_targets.json", "w"),
          ensure_ascii=False, indent=1)
json.dump(other, open("delist_bodies_other_targets.json", "w"),
          ensure_ascii=False, indent=1)
print("\nзаписано: delist_bodies_usdt_targets.json / delist_bodies_other_targets.json")
