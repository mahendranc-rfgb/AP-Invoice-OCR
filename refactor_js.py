import re

js_path = r'd:\Documents\AP Invoice OCR\app\static\app.js'
with open(js_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix formatDateForInput
date_old = """    const formatDateForInput = (dStr) => {
      if (!dStr) return "";
      try {
        if (typeof dStr === 'string' && dStr.length === 8 && /^\d{8}$/.test(dStr)) {
          return `${dStr.substring(0,4)}-${dStr.substring(4,6)}-${dStr.substring(6,8)}`;
        }
        const d = new Date(dStr);
        if (isNaN(d.getTime())) return dStr;
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
      } catch(e) {
        return dStr;
      }
    };"""

date_new = """    const formatDateForInput = (dStr) => {
      if (!dStr) return "";
      try {
        if (typeof dStr === 'string') {
          if (dStr.length === 8 && /^\d{8}$/.test(dStr)) {
            return `${dStr.substring(0,4)}-${dStr.substring(4,6)}-${dStr.substring(6,8)}`;
          }
          let cleanStr = dStr;
          if (cleanStr.includes('T')) cleanStr = cleanStr.split('T')[0];
          if (cleanStr.includes(' ')) cleanStr = cleanStr.split(' ')[0];
          if (/^\d{4}-\d{2}-\d{2}$/.test(cleanStr)) return cleanStr;
        }
        const d = new Date(dStr);
        if (isNaN(d.getTime())) return dStr;
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
      } catch(e) {
        return dStr;
      }
    };"""

content = content.replace(date_old, date_new)

# Fix line conditional checking in populateFormWithInvoice
line_old = """        if (line.location_code) setRowInput("location_code", line.location_code);
        if (line.wtax_liable) setRowInput("wtax_liable", line.wtax_liable);
        if (line.costing_code) setRowInput("costing_code", line.costing_code);
        if (line.costing_code2) setRowInput("costing_code2", line.costing_code2);
        if (line.costing_code3) setRowInput("costing_code3", line.costing_code3);"""

line_new = """        if (line.location_code !== undefined && line.location_code !== null && line.location_code !== "") setRowInput("location_code", String(line.location_code).trim());
        if (line.wtax_liable !== undefined && line.wtax_liable !== null && line.wtax_liable !== "") setRowInput("wtax_liable", line.wtax_liable);
        if (line.costing_code !== undefined && line.costing_code !== null && line.costing_code !== "") setRowInput("costing_code", String(line.costing_code).trim());
        if (line.costing_code2 !== undefined && line.costing_code2 !== null && line.costing_code2 !== "") setRowInput("costing_code2", String(line.costing_code2).trim());
        if (line.costing_code3 !== undefined && line.costing_code3 !== null && line.costing_code3 !== "") setRowInput("costing_code3", String(line.costing_code3).trim());"""

content = content.replace(line_old, line_new)

# Fix sac entry mapping logic inside populateFormWithInvoice
sac_old = """        if (line.sac_entry) {
          let sacValToSet = line.sac_entry;
          if (window.sacEntriesCache) {
             const matchedSac = window.sacEntriesCache.find(s => String(s.code) === String(line.sac_entry));
             if (matchedSac && matchedSac.extra_data) {
                sacValToSet = matchedSac.extra_data;
             }
          }
          setRowInput("sac_entry", sacValToSet);
        }"""

sac_new = """        if (line.sac_entry !== undefined && line.sac_entry !== null && line.sac_entry !== "") {
          let sacValToSet = String(line.sac_entry).trim();
          if (window.sacEntriesCache) {
             const matchedSac = window.sacEntriesCache.find(s => String(s.code).trim() === sacValToSet);
             if (matchedSac && matchedSac.extra_data) {
                sacValToSet = matchedSac.extra_data;
             } else if (matchedSac) {
                sacValToSet = String(matchedSac.code);
             }
          }
          setRowInput("sac_entry", sacValToSet);
        }"""

content = content.replace(sac_old, sac_new)

# Fix setRowInput logic for select dropdowns to correctly trim
set_row_old = """          if (input.tagName.toLowerCase() === "select") {
            const optionExists = Array.from(input.options).some(opt => opt.value === String(val));
            if (!optionExists && val !== null && val !== undefined && val !== "") {
              const newOpt = document.createElement("option");
              newOpt.value = val;
              newOpt.textContent = val;"""

set_row_new = """          if (input.tagName.toLowerCase() === "select") {
            const optionExists = Array.from(input.options).some(opt => String(opt.value).trim() === String(val).trim());
            if (!optionExists && val !== null && val !== undefined && val !== "") {
              const newOpt = document.createElement("option");
              newOpt.value = String(val).trim();
              newOpt.textContent = String(val).trim();"""

content = content.replace(set_row_old, set_row_new)

with open(js_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated app.js")
