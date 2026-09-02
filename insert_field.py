import sqlite3
import os

db_path = os.path.join("app", "data", "invoices.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute('''
    INSERT OR IGNORE INTO form_fields (field_id, section, sap_param_name, label, field_type, enabled, required, sort_order, visible) 
    VALUES ('document_date', 'header', 'TaxDate', 'Document Date', 'date', 1, 0, 6, 1)
''')
conn.commit()
conn.close()
print("Inserted document_date")
