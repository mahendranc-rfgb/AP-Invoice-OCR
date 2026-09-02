import re
import os

with open('app/static/app.js', encoding='utf-8') as f:
    js = f.read()

ids = re.findall(r'getElementById\([\'"]([^\'"]+)[\'"]\)', js)

with open('app/static/index.html', encoding='utf-8') as f:
    html = f.read()

missing = set()
for i in ids:
    if f'id="{i}"' not in html and f"id='{i}'" not in html:
        missing.add(i)

print('Missing IDs:', missing)
