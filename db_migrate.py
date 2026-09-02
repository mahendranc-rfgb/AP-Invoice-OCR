import sqlite3
import os

db_path = r"d:\Documents\AP Invoice OCR\data\invoices.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE sap_vendor_master ADD COLUMN gstin VARCHAR(50);")
    conn.commit()
    print("Migration successful")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e).lower():
        print("Column already exists")
    else:
        print(f"Error: {e}")

conn.close()
