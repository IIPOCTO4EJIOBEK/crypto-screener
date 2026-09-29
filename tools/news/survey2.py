import json, re, collections
a48 = json.load(open("announcements.json"))["48"]

print("=== записи, содержащие 'Will List' ===")
n=0
for x in a48:
    if "Will List" in x["title"]:
        n+=1
print("всего:", n)
# distinct non-date part
seen=collections.Counter()
for x in a48:
    if "Will List" in x["title"]:
        t = re.sub(r"\s*[\(\-–]?\s*\d{4}-\d{2}-\d{2}\)?\s*$","",x["title"]).strip()
        t = re.sub(r"\s*-\s*\d{4}-\d{2}-\d{2}\s*$","",t).strip()
        seen[t]+=1
for t,c in seen.most_common(200):
    print(f"{c:3}  {t}")
