import json, re, collections
d = json.load(open("announcements.json"))
a48 = d["48"]
# normalize digits for pattern counting
def norm(t):
    t = re.sub(r"\(?\d{4}-\d{2}-\d{2}\)?", "(DATE)", t)
    t = re.sub(r"\b\d+\b", "N", t)
    return t
c = collections.Counter(norm(x["title"]) for x in a48)
for t, n in c.most_common(40):
    print(f"{n:5}  {t[:130]}")
print("---- distinct patterns:", len(c), "records:", len(a48))
