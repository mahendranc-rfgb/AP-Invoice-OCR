import sqlite3
import os

db_path = 'data/invoices.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cur.fetchall()
    
    for table in tables:
        table_name = table[0]
        if table_name != 'sqlite_sequence':
            cur.execute(f"DELETE FROM {table_name};")
            
    conn.commit()
    conn.close()
    print('All tables cleared successfully.')
else:
    print('Database not found.')
