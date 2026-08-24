import codecs
js_code = r'''
// -- APPENDED RESTORED LOGIC --

// 1. switchAdminSubtab override
window.switchAdminSubtab = function(tabKey) {
  ['config', 'master', 'form', 'validations', 'users', 'ai', 'training'].forEach(k => {
    const btn = document.getElementById('subtab-admin-' + k);
    const sec = document.getElementById('admin-subtab-' + k + '-sec');
    if (btn) btn.classList.toggle('active', k === tabKey);
    if (sec) sec.classList.toggle('hidden', k !== tabKey);
  });
  if (tabKey === 'training' && typeof fetchAiTrainingStats === 'function') {
    fetchAiTrainingStats();
  }
  if (tabKey === 'validations') {
    loadValidationRules();
  }
};

// 2. Validation Rules Logic
window.loadValidationRules = async function() {
    try {
        const res = await fetch('/admin/validation-rules');
        const rules = await res.json();
        const tbody = document.getElementById('admin-validations-tbody');
        if (!tbody) return;
        
        if (!rules || rules.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">No validation rules found.</td></tr>';
            return;
        }
        
        tbody.innerHTML = rules.map(r => 
            '<tr>' +
                '<td>' + r.rule_name + '</td>' +
                '<td><span class="pill neutral" style="font-family: monospace;">' + r.target_field + '</span></td>' +
                '<td><strong>' + r.condition + '</strong></td>' +
                '<td>' + (r.condition_value || '<em>(empty)</em>') + '</td>' +
                '<td>' +
                    '<span class="pill ' + (r.is_active ? 'success' : 'danger') + '">' + (r.is_active ? 'Active' : 'Inactive') + '</span>' +
                '</td>' +
                '<td>' +
                    '<button class="sap-btn outline small" onclick=\'editValidationRule(' + JSON.stringify(r) + ')\'>✏️ Edit</button> ' +
                    '<button class="sap-btn outline small" style="color:var(--accent-rose); border-color:var(--accent-rose);" onclick="deleteValidationRule(' + r.id + ', \'' + r.rule_name.replace(/'/g, "\\'") + '\')\">🗑️ Delete</button>' +
                '</td>' +
            '</tr>'
        ).join('');
    } catch (e) {
        console.error(e);
        showToast('Failed to load validation rules', 'danger');
    }
};

window.saveValidationRule = async function(e) {
    e.preventDefault();
    const payload = {
        id: document.getElementById('vr-id').value ? parseInt(document.getElementById('vr-id').value) : null,
        rule_name: document.getElementById('vr-name').value,
        target_field: document.getElementById('vr-field').value,
        condition: document.getElementById('vr-condition').value,
        condition_value: document.getElementById('vr-value').value,
        error_message: document.getElementById('vr-message').value,
        is_active: document.getElementById('vr-active').checked
    };
    
    try {
        const res = await fetch('/admin/validation-rules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Validation rule saved successfully', 'success');
            document.getElementById('admin-validation-form').reset();
            document.getElementById('vr-id').value = '';
            loadValidationRules();
        } else {
            showToast(data.detail || 'Failed to save rule', 'danger');
        }
    } catch (err) {
        showToast('Error saving rule', 'danger');
    }
};

window.editValidationRule = function(r) {
    document.getElementById('vr-id').value = r.id;
    document.getElementById('vr-name').value = r.rule_name;
    document.getElementById('vr-field').value = r.target_field;
    document.getElementById('vr-condition').value = r.condition;
    document.getElementById('vr-value').value = r.condition_value;
    document.getElementById('vr-message').value = r.error_message;
    document.getElementById('vr-active').checked = r.is_active;
};

window.deleteValidationRule = async function(id, name) {
    if (!confirm('Are you sure you want to delete the rule "' + name + '"?')) return;
    try {
        const res = await fetch('/admin/validation-rules/' + id, { method: 'DELETE' });
        if (res.ok) {
            showToast('Rule deleted', 'success');
            loadValidationRules();
        } else {
            showToast('Failed to delete rule', 'danger');
        }
    } catch (err) {
        showToast('Error deleting rule', 'danger');
    }
};

// 3. Open Documents Logic
window.loadAllOpenDocs = async function() {
    try {
        const res = await fetch('/admin/sap-open-docs');
        if (!res.ok) throw new Error();
        const data = await res.json();
        const tbody = document.getElementById('all-open-docs-tbody');
        if (!tbody) return;
        
        if (data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">No open documents found. Try syncing from SAP.</td></tr>';
            return;
        }
        
        let html = '';
        data.forEach(d => {
            html += '<tr>';
            html += '<td><span class="pill neutral">' + d.doc_type + '</span></td>';
            html += '<td><code style="color:var(--sap-gold);">' + d.doc_num + '</code></td>';
            html += '<td>' + d.vendor_code + '</td>';
            html += '<td>' + new Date(d.doc_date).toLocaleDateString() + '</td>';
            html += '<td><strong>' + parseFloat(d.total_amount).toFixed(2) + '</strong></td>';
            
            const payload = JSON.stringify(d).replace(/"/g, '&quot;');
            html += '<td><button class="sap-btn gold small" onclick="loadDocumentIntoForm(this)" data-payload="' + payload + '">Load into Form</button></td>';
            html += '</tr>';
        });
        tbody.innerHTML = html;
        filterOpenDocs(); // Re-apply filter
    } catch (e) {
        const tbody = document.getElementById('all-open-docs-tbody');
        if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">Error loading open documents.</td></tr>';
    }
};

window.syncOpenDocsFromSAP = async function(docType) {
    const btn = document.getElementById('btn-sync-open-' + docType.toLowerCase() + 's');
    if (!btn) return;
    const originalText = btn.innerHTML;
    try {
        btn.innerHTML = 'Syncing...';
        btn.disabled = true;
        const res = await fetch('/admin/sap-sync-open-docs?doc_type=' + docType, {
            method: 'POST'
        });
        const data = await res.json();
        
        if (data.status === 'ok') {
            showToast(data.message, 'success');
            loadAllOpenDocs();
        } else {
            showToast(data.detail || ('Error syncing open ' + docType + 's'), 'danger');
        }
    } catch (e) {
        console.error('Error syncing open documents:', e);
        showToast('Error syncing open documents', 'danger');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
};

window.filterOpenDocs = function() {
    const searchInput = document.getElementById('search-open-docs');
    const typeInput = document.getElementById('filter-doc-type');
    if (!searchInput) return;
    const filterText = searchInput.value.toLowerCase();
    const filterType = typeInput ? typeInput.value : '';
    const tbody = document.getElementById('all-open-docs-tbody');
    if (!tbody) return;
    const rows = tbody.getElementsByTagName('tr');
    
    for (let i = 0; i < rows.length; i++) {
        if (rows[i].getElementsByTagName('td').length === 1) continue;
        
        const typeCell = rows[i].getElementsByTagName('td')[0];
        const rowType = typeCell ? typeCell.textContent.trim() : '';
        const textValue = rows[i].textContent || rows[i].innerText;
        
        const matchText = textValue.toLowerCase().indexOf(filterText) > -1;
        const matchType = filterType === '' || rowType === filterType;
        
        if (matchText && matchType) {
            rows[i].style.display = '';
        } else {
            rows[i].style.display = 'none';
        }
    }
};

window.loadDocumentIntoForm = function(btnEl) {
    const payloadStr = btnEl.getAttribute('data-payload');
    if (!payloadStr) return;
    const docData = JSON.parse(payloadStr);
    
    showToast('Loading ' + docData.doc_type + ' ' + docData.doc_num + ' into memory...', 'info');
    
    console.log('Loaded Document:', docData);
    showToast(docData.doc_type + ' loaded. You can now use the extracted grid lines.', 'success');
};
'''
with codecs.open(r'c:\Users\DELL\Documents\AP Invoice OCR\app\static\app.js', 'a', encoding='utf-8') as f:
    f.write('\n' + js_code)
print('Code appended successfully!')
