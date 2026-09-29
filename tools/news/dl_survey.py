import json, re, collections
from datetime import datetime, timezone
a = json.load(open("announcements.json"))["161"]
f = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
def norm(t):
    t = re.sub(r"\(?\d{4}-\d{2}-\d{2}\)?", "(DATE)", t); t = re.sub(r"\b\d+\b","N",t); return t
c=collections.Counter(norm(x["title"]) for x in a)
print("записей:", len(a), f(a[-1]['releaseDate']), "..", f(a[0]['releaseDate']))
for t,n in c.most_common(18): print(f"{n:4}  {t[:125]}")
print()
print("=== примеры сырых заголовков ===")
for x in a[:14]: print("  ", f(x['releaseDate']), "|", x['title'][:110])
