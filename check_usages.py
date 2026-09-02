import re

with open('app/static/app.js', encoding='utf-8') as f:
    js = f.read()

ids = ['addr-type-input', 'erp-doc-num-display', 'auth-role-input', 'btn-validate', 'select-ship-to-code', 'wtax-desc-input', 'wtax-type-input', 'addr-code-input', 'wtax-code-input', 'admin-addresses-tbody', 'memory-game-board', 'wtax-rate-input', 'btn-map', 'memory-game-message', 'addr-default-input', 'wtax-active-input', 'addr-vendor-code-input', 'admin-wtax-tbody', 'addr-text-input']
vars = {}

for i in ids:
    match = re.search(r'(?:const|let|var)\s+(\w+)\s*=\s*document\.getElementById\([\'"]' + i + r'[\'"]\)', js)
    if match:
        vars[i] = match.group(1)
        
for i, v in vars.items():
    usages = re.findall(r'\b' + v + r'\.(?!getElementById)\w+', js)
    if usages:
        print(f'{v} ({i}) is used: {set(usages)}')
