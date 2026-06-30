import json
d = json.load(open(r'C:\Users\norman\AppData\Local\Temp\radar-24h.json'))
print(f"数据时间: {d['generated_at'][:19]} | 总条目: {d['total_items']} | 信源: {d['site_count']}个\n")

items = d['items_ai']
top = sorted(items, key=lambda i: (i['source_tier_rank'], -i['ai_score']))[:30]

for i in top:
    label = i['ai_label']
    title = i['title'][:80]
    source = i['source']
    url = i['url'][:80]
    print(f"[{label}] {title} — {source} — {url}")
