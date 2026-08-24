from __future__ import annotations

import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.models import InvoiceDocument


class DatabaseRepository:
    """Unified Database Repository supporting MSSQL, MySQL 8.0, and SQLite."""

    def __init__(
        self,
                database_path: Path | None = None,
        db_engine: str = "sqlite",
        mysql_host: str = "127.0.0.1",
        mysql_port: int = 3306,
        mysql_database: str = "ap_invoice_ocr",
        mysql_user: str = "ap_user",
        mysql_password: str = "ap_password_123",
        mssql_host: str = "127.0.0.1",
        mssql_port: int = 1433,
        mssql_database: str = "ap_invoice_ocr",
        mssql_user: str = "sa",
        mssql_password: str = "yourStrong(!)Password",
    ) -> None:
        self.db_engine = db_engine.lower()
        self.database_path = database_path or Path("data/invoices.db")
        self.mysql_host = mysql_host
        self.mysql_port = mysql_port
        self.mysql_database = mysql_database
        self.mysql_user = mysql_user
        self.mysql_password = mysql_password
        self.mssql_host = mssql_host
        self.mssql_port = mssql_port
        self.mssql_database = mssql_database
        self.mssql_user = mssql_user
        self.mssql_password = mssql_password

        if self.db_engine == "mssql":
            try:
                self._ensure_mssql_db_exists()
            except Exception as err:
                print(f"[-] MSSQL connection failed ({err}), falling back to SQLite local database.")
                self.db_engine = "sqlite"
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
        elif self.db_engine == "mysql":
            try:
                self._ensure_mysql_db_exists()
            except Exception as err:
                print(f"[-] MySQL connection failed ({err}), falling back to SQLite local database.")
                self.db_engine = "sqlite"
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)

        self.init_db()

    def _ensure_mssql_db_exists(self) -> None:
        import pymssql
        connection = pymssql.connect(
            server=self.mssql_host,
            port=str(self.mssql_port),
            user=self.mssql_user,
            password=self.mssql_password,
            autocommit=True,
            timeout=3
        )
        with connection.cursor() as cursor:
            cursor.execute(f"IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = '{self.mssql_database}') CREATE DATABASE [{self.mssql_database}]")
        connection.close()

    def _ensure_mysql_db_exists(self) -> None:
        import pymysql
        connection = pymysql.connect(
            host=self.mysql_host,
            port=self.mysql_port,
            user=self.mysql_user,
            password=self.mysql_password,
            autocommit=True,
            connect_timeout=3,
        )
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.mysql_database}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        connection.close()

    def _connect(self):
        if self.db_engine == "mssql":
            try:
                import pymssql
                return pymssql.connect(
                    server=self.mssql_host,
                    port=str(self.mssql_port),
                    user=self.mssql_user,
                    password=self.mssql_password,
                    database=self.mssql_database,
                    autocommit=True,
                    timeout=3
                )
            except Exception:
                self.db_engine = "sqlite"
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                return sqlite3.connect(self.database_path)
        elif self.db_engine == "mysql":
            try:
                import pymysql
                return pymysql.connect(
                    host=self.mysql_host,
                    port=self.mysql_port,
                    user=self.mysql_user,
                    password=self.mysql_password,
                    database=self.mysql_database,
                    autocommit=True,
                    connect_timeout=3,
                )
            except Exception:
                self.db_engine = "sqlite"
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                return sqlite3.connect(self.database_path)
        else:
            return sqlite3.connect(self.database_path)

    def _execute_sql(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        connection = self._connect()
        try:
            if self.db_engine in ["mysql", "mssql"]:
                sql_dialect = sql.replace("?", "%s")
                with connection.cursor() as cursor:
                    cursor.execute(sql_dialect, params)
                    if cursor.description:
                        return cursor.fetchall()
                    return []
            else:
                with connection:
                    cursor = connection.execute(sql, params)
                    return cursor.fetchall()
        finally:
            connection.close()

    def init_db(self) -> None:
        if self.db_engine == "mssql":
            ddls = [
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='documents' and xtype='U')
                   CREATE TABLE documents (
                    document_id VARCHAR(64) PRIMARY KEY,
                    card_code VARCHAR(64),
                    invoice_number VARCHAR(128) NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    payload NVARCHAR(MAX) NOT NULL
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='posted_keys' and xtype='U')
                   CREATE TABLE posted_keys (
                    card_code VARCHAR(64) NOT NULL,
                    invoice_number VARCHAR(128) NOT NULL,
                    PRIMARY KEY(card_code, invoice_number)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='mapping_history' and xtype='U')
                   CREATE TABLE mapping_history (
                    history_id INT IDENTITY(1,1) PRIMARY KEY,
                    supplier_name VARCHAR(255),
                    ocr_value VARCHAR(255) NOT NULL,
                    ai_value VARCHAR(255),
                    final_correct_value VARCHAR(255) NOT NULL,
                    mapping_type VARCHAR(64) NOT NULL,
                    field_payload NVARCHAR(MAX),
                    correction_date VARCHAR(64) NOT NULL
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='sap_register_vendors' and xtype='U')
                   CREATE TABLE sap_register_vendors (
                    card_code VARCHAR(64) PRIMARY KEY,
                    card_name VARCHAR(255)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='sap_register_items' and xtype='U')
                   CREATE TABLE sap_register_items (
                    item_code VARCHAR(64) PRIMARY KEY,
                    item_name VARCHAR(255)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='custom_master_data' and xtype='U')
                   CREATE TABLE custom_master_data (
                    category VARCHAR(64) NOT NULL,
                    code VARCHAR(128) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    extra_data NVARCHAR(MAX),
                    is_default INT DEFAULT 0,
                    PRIMARY KEY (category, code)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='app_users' and xtype='U')
                   CREATE TABLE app_users (
                    username VARCHAR(64) PRIMARY KEY,
                    password VARCHAR(255) NOT NULL,
                    role VARCHAR(64) NOT NULL,
                    created_at VARCHAR(64) NOT NULL
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='form_fields' and xtype='U')
                   CREATE TABLE form_fields (
                    field_id VARCHAR(64) PRIMARY KEY,
                    section VARCHAR(64) NOT NULL,
                    sap_param_name VARCHAR(128) NOT NULL,
                    label VARCHAR(128) NOT NULL,
                    field_type VARCHAR(64) NOT NULL,
                    enabled INT NOT NULL DEFAULT 1,
                    required INT NOT NULL DEFAULT 0,
                    sort_order INT NOT NULL DEFAULT 0,
                    visible INT NOT NULL DEFAULT 1
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='uploads' and xtype='U')
                   CREATE TABLE uploads (
                    upload_id VARCHAR(64) PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    stored_path VARCHAR(512) NOT NULL,
                    content_type VARCHAR(128) NOT NULL,
                    extraction_status VARCHAR(64) NOT NULL,
                    extracted_text NVARCHAR(MAX) NOT NULL,
                    invoice_payload NVARCHAR(MAX) NOT NULL,
                    created_at VARCHAR(64) NOT NULL
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='wtax_codes' and xtype='U')
                   CREATE TABLE wtax_codes (
                    wtax_code VARCHAR(32) PRIMARY KEY,
                    description VARCHAR(255) NOT NULL,
                    wtax_rate DECIMAL(6,2) NOT NULL DEFAULT 0.00,
                    wtax_type VARCHAR(8) NOT NULL DEFAULT 'G',
                    active INT NOT NULL DEFAULT 1
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='vendor_addresses' and xtype='U')
                   CREATE TABLE vendor_addresses (
                    vendor_code VARCHAR(64) NOT NULL,
                    address_code VARCHAR(128) NOT NULL,
                    address_type VARCHAR(8) NOT NULL DEFAULT 'ship',
                    address_text NVARCHAR(MAX) NOT NULL,
                    address2 VARCHAR(255),
                    address3 VARCHAR(255),
                    block VARCHAR(128),
                    building VARCHAR(128),
                    street VARCHAR(255),
                    street_no VARCHAR(128),
                    city VARCHAR(128),
                    country VARCHAR(128),
                    state VARCHAR(128),
                    gst_regn_no VARCHAR(64),
                    is_default INT NOT NULL DEFAULT 0,
                    PRIMARY KEY (vendor_code, address_code, address_type)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='sap_vendor_master' and xtype='U')
                   CREATE TABLE sap_vendor_master (
                    card_code VARCHAR(64) PRIMARY KEY,
                    group_name VARCHAR(128),
                    payment_group VARCHAR(128),
                    extra_days INT,
                    currency VARCHAR(8),
                    balance DECIMAL(12,2),
                    balance_fc DECIMAL(12,2)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='open_po' and xtype='U')
                   CREATE TABLE open_po (
                    doc_entry VARCHAR(64) PRIMARY KEY,
                    doc_num VARCHAR(128) NOT NULL,
                    vendor_code VARCHAR(64) NOT NULL,
                    doc_date VARCHAR(64),
                    total_amount DECIMAL(12,2),
                    lines_payload NVARCHAR(MAX)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='open_grn' and xtype='U')
                   CREATE TABLE open_grn (
                    doc_entry VARCHAR(64) PRIMARY KEY,
                    doc_num VARCHAR(128) NOT NULL,
                    vendor_code VARCHAR(64) NOT NULL,
                    doc_date VARCHAR(64),
                    total_amount DECIMAL(12,2),
                    lines_payload NVARCHAR(MAX)
                );""",
                """IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='validation_rules' and xtype='U')
                   CREATE TABLE validation_rules (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    rule_name VARCHAR(128) NOT NULL,
                    target_field VARCHAR(128) NOT NULL,
                    condition VARCHAR(64) NOT NULL,
                    condition_value VARCHAR(255),
                    error_message VARCHAR(512) NOT NULL,
                    is_active INT NOT NULL DEFAULT 1
                );"""
            ]
            for ddl in ddls:
                self._execute_sql(ddl)
            

            try:
                self._execute_sql("ALTER TABLE form_fields ADD visible INT DEFAULT 1;")
            except Exception:
                pass
            self._execute_sql("IF NOT EXISTS (SELECT * FROM app_users WHERE username='admin') INSERT INTO app_users (username, password, role, created_at) VALUES (?, ?, ?, ?);", ("admin", "admin", "admin", "2026-01-01T00:00:00Z"))
            self._execute_sql("IF NOT EXISTS (SELECT * FROM app_users WHERE username='user') INSERT INTO app_users (username, password, role, created_at) VALUES (?, ?, ?, ?);", ("user", "user", "user", "2026-01-01T00:00:00Z"))

            fields = [
                ('supplier_name', 'header', 'CardName', 'Supplier Name', 'text', 1, 1, 1),
                ('sap_card_code', 'header', 'CardCode', 'Vendor Code', 'text', 1, 1, 2),
                ('supplier_gstin', 'header', 'BPGSTIN', 'Supplier GSTIN', 'text', 1, 0, 3),
                ('invoice_number', 'header', 'NumAtCard', 'Vendor Ref / Inv #', 'text', 1, 1, 4),
                ('invoice_date', 'header', 'DocDate', 'Invoice Date', 'date', 1, 1, 5),
                ('posting_date', 'header', 'TaxDate', 'Posting Date', 'date', 1, 0, 6),
                ('due_date', 'header', 'DocDueDate', 'Due Date', 'date', 1, 0, 7),
                ('local_currency', 'header', 'DocCurrency', 'Currency', 'select', 1, 1, 8),
                ('series', 'header', 'Series', 'Series', 'select', 1, 1, 9),
                ('bpl_id_assigned_to_invoice', 'header', 'BPL_IDAssignedToInvoice', 'Branch (BPL_ID)', 'select', 1, 1, 10),
                ('transaction_type', 'header', 'GSTTransactionType', 'GST TransType', 'select', 1, 0, 11),
                ('comments', 'header', 'Comments', 'Comments', 'textarea', 1, 0, 12),
                ('description', 'lines', 'ItemDescription', 'Description', 'text', 1, 1, 1),
                ('sap_item_code', 'lines', 'ItemCode', 'Item Code', 'text', 1, 0, 2),
                ('gl_account', 'lines', 'AccountCode', 'G/L Account', 'select', 1, 1, 3),
                ('sac_entry', 'lines', 'SACEntry', 'SAC Entry', 'text', 1, 0, 4),
                ('quantity', 'lines', 'Quantity', 'Quantity', 'number', 1, 1, 5),
                ('unit_price', 'lines', 'UnitPrice', 'Unit Price', 'number', 1, 1, 6),
                ('tax_percentage', 'lines', 'TaxPercentage', 'Tax %', 'number', 1, 1, 7),
                ('tax_code', 'lines', 'TaxCode', 'Tax Code', 'select', 1, 1, 8),
                ('location_code', 'lines', 'LocationCode', 'Location Code', 'text', 1, 0, 9),
                ('costing_code', 'lines', 'CostingCode', 'Cost Center 1', 'select', 1, 0, 10),
                ('costing_code2', 'lines', 'CostingCode2', 'Cost Center 2', 'select', 1, 0, 11),
                ('costing_code3', 'lines', 'CostingCode3', 'Cost Center 3', 'select', 1, 0, 12),
            ]
            for f in fields:
                self._execute_sql("IF NOT EXISTS (SELECT * FROM form_fields WHERE field_id=?) INSERT INTO form_fields (field_id, section, sap_param_name, label, field_type, enabled, required, sort_order, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);", (f[0], *f, 1))
            
            try:
                self._execute_sql("""IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='app_settings' and xtype='U')
                   CREATE TABLE app_settings (
                    setting_key VARCHAR(64) PRIMARY KEY,
                    setting_value NVARCHAR(MAX)
                );""")
            except Exception:
                pass
        elif self.db_engine == "mysql":
            ddls = [
                """CREATE TABLE IF NOT EXISTS documents (
                    document_id VARCHAR(64) PRIMARY KEY,
                    card_code VARCHAR(64),
                    invoice_number VARCHAR(128) NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    payload LONGTEXT NOT NULL
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS posted_keys (
                    card_code VARCHAR(64) NOT NULL,
                    invoice_number VARCHAR(128) NOT NULL,
                    PRIMARY KEY(card_code, invoice_number)
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS mapping_history (
                    history_id INT AUTO_INCREMENT PRIMARY KEY,
                    supplier_name VARCHAR(255),
                    ocr_value VARCHAR(255) NOT NULL,
                    ai_value VARCHAR(255),
                    final_correct_value VARCHAR(255) NOT NULL,
                    mapping_type VARCHAR(64) NOT NULL,
                    field_payload TEXT,
                    correction_date VARCHAR(64) NOT NULL
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS sap_register_vendors (
                    card_code VARCHAR(64) PRIMARY KEY,
                    card_name VARCHAR(255)
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS sap_register_items (
                    item_code VARCHAR(64) PRIMARY KEY,
                    item_name VARCHAR(255)
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS custom_master_data (
                    category VARCHAR(64) NOT NULL,
                    code VARCHAR(128) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    extra_data TEXT,
                    is_default INT DEFAULT 0,
                    PRIMARY KEY (category, code)
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS app_users (
                    username VARCHAR(64) PRIMARY KEY,
                    password VARCHAR(255) NOT NULL,
                    role VARCHAR(64) NOT NULL,
                    created_at VARCHAR(64) NOT NULL
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS form_fields (
                    field_id VARCHAR(64) PRIMARY KEY,
                    section VARCHAR(64) NOT NULL,
                    sap_param_name VARCHAR(128) NOT NULL,
                    label VARCHAR(128) NOT NULL,
                    field_type VARCHAR(64) NOT NULL,
                    enabled INT NOT NULL DEFAULT 1,
                    required INT NOT NULL DEFAULT 0,
                    sort_order INT NOT NULL DEFAULT 0,
                    visible INT NOT NULL DEFAULT 1
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS uploads (
                    upload_id VARCHAR(64) PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    stored_path VARCHAR(512) NOT NULL,
                    content_type VARCHAR(128) NOT NULL,
                    extraction_status VARCHAR(64) NOT NULL,
                    extracted_text LONGTEXT NOT NULL,
                    invoice_payload LONGTEXT NOT NULL,
                    created_at VARCHAR(64) NOT NULL
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS wtax_codes (
                    wtax_code VARCHAR(32) PRIMARY KEY,
                    description VARCHAR(255) NOT NULL,
                    wtax_rate DECIMAL(6,2) NOT NULL DEFAULT 0.00,
                    wtax_type VARCHAR(8) NOT NULL DEFAULT 'G',
                    active INT NOT NULL DEFAULT 1
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS vendor_addresses (
                    vendor_code VARCHAR(64) NOT NULL,
                    address_code VARCHAR(128) NOT NULL,
                    address_type VARCHAR(8) NOT NULL DEFAULT 'ship',
                    address_text TEXT NOT NULL,
                    address2 VARCHAR(255),
                    address3 VARCHAR(255),
                    block VARCHAR(128),
                    building VARCHAR(128),
                    street VARCHAR(255),
                    street_no VARCHAR(128),
                    city VARCHAR(128),
                    country VARCHAR(128),
                    state VARCHAR(128),
                    gst_regn_no VARCHAR(64),
                    is_default INT NOT NULL DEFAULT 0,
                    PRIMARY KEY (vendor_code, address_code, address_type)
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS sap_vendor_master (
                    card_code VARCHAR(64) PRIMARY KEY,
                    group_name VARCHAR(128),
                    payment_group VARCHAR(128),
                    extra_days INT,
                    currency VARCHAR(8),
                    balance DECIMAL(12,2),
                    balance_fc DECIMAL(12,2)
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS open_po (
                    doc_entry VARCHAR(64) PRIMARY KEY,
                    doc_num VARCHAR(128) NOT NULL,
                    vendor_code VARCHAR(64) NOT NULL,
                    doc_date VARCHAR(64),
                    total_amount DECIMAL(12,2),
                    lines_payload LONGTEXT
                ) ENGINE=InnoDB;""",
                """CREATE TABLE IF NOT EXISTS open_grn (
                    doc_entry VARCHAR(64) PRIMARY KEY,
                    doc_num VARCHAR(128) NOT NULL,
                    vendor_code VARCHAR(64) NOT NULL,
                    doc_date VARCHAR(64),
                    total_amount DECIMAL(12,2),
                    lines_payload LONGTEXT
                ) ENGINE=InnoDB;""",
            ]
            for ddl in ddls:
                self._execute_sql(ddl)


            try:
                self._execute_sql("ALTER TABLE form_fields ADD COLUMN visible INT DEFAULT 1;")
            except Exception:
                pass
            # Seed default users
            self._execute_sql("INSERT IGNORE INTO app_users VALUES (%s, %s, %s, %s);", ("admin", "admin", "admin", "2026-01-01T00:00:00Z"))
            self._execute_sql("INSERT IGNORE INTO app_users VALUES (%s, %s, %s, %s);", ("user", "user", "user", "2026-01-01T00:00:00Z"))

            # Seed form fields
            fields = [
                ('supplier_name', 'header', 'CardName', 'Supplier Name', 'text', 1, 1, 1),
                ('sap_card_code', 'header', 'CardCode', 'Vendor Code', 'text', 1, 1, 2),
                ('supplier_gstin', 'header', 'BPGSTIN', 'Supplier GSTIN', 'text', 1, 0, 3),
                ('invoice_number', 'header', 'NumAtCard', 'Vendor Ref / Inv #', 'text', 1, 1, 4),
                ('invoice_date', 'header', 'DocDate', 'Invoice Date', 'date', 1, 1, 5),
                ('posting_date', 'header', 'TaxDate', 'Posting Date', 'date', 1, 0, 6),
                ('due_date', 'header', 'DocDueDate', 'Due Date', 'date', 1, 0, 7),
                ('local_currency', 'header', 'DocCurrency', 'Currency', 'select', 1, 1, 8),
                ('series', 'header', 'Series', 'Series', 'select', 1, 1, 9),
                ('bpl_id_assigned_to_invoice', 'header', 'BPL_IDAssignedToInvoice', 'Branch (BPL_ID)', 'select', 1, 1, 10),
                ('transaction_type', 'header', 'GSTTransactionType', 'GST TransType', 'select', 1, 0, 11),
                ('comments', 'header', 'Comments', 'Comments', 'textarea', 1, 0, 12),
                ('description', 'lines', 'ItemDescription', 'Description', 'text', 1, 1, 1),
                ('sap_item_code', 'lines', 'ItemCode', 'Item Code', 'text', 1, 0, 2),
                ('gl_account', 'lines', 'AccountCode', 'G/L Account', 'select', 1, 1, 3),
                ('sac_entry', 'lines', 'SACEntry', 'SAC Entry', 'text', 1, 0, 4),
                ('quantity', 'lines', 'Quantity', 'Quantity', 'number', 1, 1, 5),
                ('unit_price', 'lines', 'UnitPrice', 'Unit Price', 'number', 1, 1, 6),
                ('tax_percentage', 'lines', 'TaxPercentage', 'Tax %', 'number', 1, 1, 7),
                ('tax_code', 'lines', 'TaxCode', 'Tax Code', 'select', 1, 1, 8),
                ('location_code', 'lines', 'LocationCode', 'Location Code', 'text', 1, 0, 9),
                ('costing_code', 'lines', 'CostingCode', 'Cost Center 1', 'select', 1, 0, 10),
                ('costing_code2', 'lines', 'CostingCode2', 'Cost Center 2', 'select', 1, 0, 11),
                ('costing_code3', 'lines', 'CostingCode3', 'Cost Center 3', 'select', 1, 0, 12),
                ('base_entry', 'lines', 'BaseEntry', 'Base Entry (PO)', 'number', 1, 0, 13),
                ('base_type', 'lines', 'BaseType', 'Base Type', 'number', 1, 0, 14),
                ('base_line', 'lines', 'BaseLine', 'Base Line', 'number', 1, 0, 15),
            ]
            for f in fields:
                self._execute_sql("INSERT IGNORE INTO form_fields VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);", (*f, 1))
            
            try:
                self._execute_sql("""CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key VARCHAR(64) PRIMARY KEY,
                    setting_value TEXT
                ) ENGINE=InnoDB;""")
            except Exception:
                pass
        else:
            with sqlite3.connect(self.database_path) as connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS documents (
                        document_id TEXT PRIMARY KEY,
                        card_code TEXT,
                        invoice_number TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS posted_keys (
                        card_code TEXT NOT NULL,
                        invoice_number TEXT NOT NULL,
                        PRIMARY KEY(card_code, invoice_number)
                    );
                    CREATE TABLE IF NOT EXISTS mapping_history (
                        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        supplier_name TEXT,
                        ocr_value TEXT NOT NULL,
                        ai_value TEXT,
                        final_correct_value TEXT NOT NULL,
                        mapping_type TEXT NOT NULL,
                        field_payload TEXT,
                        correction_date TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sap_register_vendors (
                        card_code TEXT PRIMARY KEY,
                        card_name TEXT
                    );
                    CREATE TABLE IF NOT EXISTS sap_register_items (
                        item_code TEXT PRIMARY KEY,
                        item_name TEXT
                    );
                    CREATE TABLE IF NOT EXISTS custom_master_data (
                        category TEXT NOT NULL,
                        code TEXT NOT NULL,
                        name TEXT NOT NULL,
                        extra_data TEXT,
                        is_default INTEGER DEFAULT 0,
                        PRIMARY KEY (category, code)
                    );
                    CREATE TABLE IF NOT EXISTS app_users (
                        username TEXT PRIMARY KEY,
                        password TEXT NOT NULL,
                        role TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS uploads (
                        upload_id TEXT PRIMARY KEY,
                        filename TEXT NOT NULL,
                        stored_path TEXT NOT NULL,
                        content_type TEXT NOT NULL,
                        extraction_status TEXT NOT NULL,
                        extracted_text TEXT NOT NULL,
                        invoice_payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS wtax_codes (
                        wtax_code TEXT PRIMARY KEY,
                        description TEXT NOT NULL,
                        wtax_rate REAL NOT NULL DEFAULT 0.0,
                        wtax_type TEXT NOT NULL DEFAULT 'G',
                        active INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE TABLE IF NOT EXISTS vendor_addresses (
                        vendor_code TEXT NOT NULL,
                        address_code TEXT NOT NULL,
                        address_type TEXT NOT NULL DEFAULT 'ship',
                        address_text TEXT NOT NULL,
                        address2 TEXT,
                        address3 TEXT,
                        block TEXT,
                        building TEXT,
                        street TEXT,
                        street_no TEXT,
                        city TEXT,
                        country TEXT,
                        state TEXT,
                        gst_regn_no TEXT,
                        is_default INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (vendor_code, address_code, address_type)
                    );
                    CREATE TABLE IF NOT EXISTS sap_vendor_master (
                        card_code TEXT PRIMARY KEY,
                        group_name TEXT,
                        payment_group TEXT,
                        extra_days INTEGER,
                        currency TEXT,
                        balance REAL,
                        balance_fc REAL
                    );
                    CREATE TABLE IF NOT EXISTS open_po (
                        doc_entry TEXT PRIMARY KEY,
                        doc_num TEXT NOT NULL,
                        vendor_code TEXT NOT NULL,
                        doc_date TEXT,
                        total_amount REAL,
                        lines_payload TEXT
                    );
                    CREATE TABLE IF NOT EXISTS open_grn (
                        doc_entry TEXT PRIMARY KEY,
                        doc_num TEXT NOT NULL,
                        vendor_code TEXT NOT NULL,
                        doc_date TEXT,
                        total_amount REAL,
                        lines_payload TEXT
                    );
                    INSERT OR IGNORE INTO app_users VALUES ('admin', 'admin', 'admin', '2026-01-01T00:00:00Z');
                    INSERT OR IGNORE INTO app_users VALUES ('user', 'user', 'user', '2026-01-01T00:00:00Z');
                    INSERT OR IGNORE INTO wtax_codes VALUES ('C004', 'Contractor / Sub-contractor (TDS 2%)', 2.00, 'G', 1);
                    INSERT OR IGNORE INTO wtax_codes VALUES ('C010', 'Professional / Technical Services (TDS 10%)', 10.00, 'G', 1);
                    INSERT OR IGNORE INTO wtax_codes VALUES ('C007', 'Rent of Plant & Machinery (TDS 2%)', 2.00, 'G', 1);
                    INSERT OR IGNORE INTO wtax_codes VALUES ('C194J', 'Professional / Royalty Fees (TDS 10%)', 10.00, 'G', 1);
                    INSERT OR IGNORE INTO wtax_codes VALUES ('C194I', 'Rent of Land / Building / Furniture (TDS 10%)', 10.00, 'G', 1);

                    CREATE TABLE IF NOT EXISTS form_fields (
                        field_id TEXT PRIMARY KEY,
                        section TEXT NOT NULL,
                        sap_param_name TEXT NOT NULL,
                        label TEXT NOT NULL,
                        field_type TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        required INTEGER NOT NULL DEFAULT 0,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        visible INTEGER NOT NULL DEFAULT 1
                    );
                    
                    -- Migration for older databases: Add visible column if missing
                """)
                try:
                    connection.execute("ALTER TABLE form_fields ADD COLUMN visible INTEGER DEFAULT 1;")
                except Exception:
                    pass
                connection.executescript("""
                    INSERT OR IGNORE INTO form_fields VALUES ('supplier_name', 'header', 'CardName', 'Supplier Name', 'text', 1, 1, 1, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('sap_card_code', 'header', 'CardCode', 'Vendor Code', 'text', 1, 1, 2, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('supplier_gstin', 'header', 'BPGSTIN', 'Supplier GSTIN', 'text', 1, 0, 3, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('invoice_number', 'header', 'NumAtCard', 'Vendor Ref / Inv #', 'text', 1, 1, 4, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('invoice_date', 'header', 'DocDate', 'Invoice Date', 'date', 1, 1, 5, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('posting_date', 'header', 'TaxDate', 'Posting Date', 'date', 1, 0, 6, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('due_date', 'header', 'DocDueDate', 'Due Date', 'date', 1, 0, 7, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('local_currency', 'header', 'DocCurrency', 'Currency', 'select', 1, 1, 8, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('series', 'header', 'Series', 'Series', 'select', 1, 1, 9, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('bpl_id_assigned_to_invoice', 'header', 'BPL_IDAssignedToInvoice', 'Branch (BPL_ID)', 'select', 1, 1, 10, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('transaction_type', 'header', 'GSTTransactionType', 'GST TransType', 'select', 1, 0, 11, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('comments', 'header', 'Comments', 'Comments', 'textarea', 1, 0, 12, 1);
                    
                    INSERT OR IGNORE INTO form_fields VALUES ('description', 'lines', 'ItemDescription', 'Description', 'text', 1, 1, 1, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('sap_item_code', 'lines', 'ItemCode', 'Item Code', 'text', 1, 0, 2, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('gl_account', 'lines', 'AccountCode', 'G/L Account', 'select', 1, 1, 3, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('sac_entry', 'lines', 'SACEntry', 'SAC Entry', 'text', 1, 0, 4, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('quantity', 'lines', 'Quantity', 'Quantity', 'number', 1, 1, 5, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('unit_price', 'lines', 'UnitPrice', 'Unit Price', 'number', 1, 1, 6, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('tax_percentage', 'lines', 'TaxPercentage', 'Tax %', 'number', 1, 1, 7, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('tax_code', 'lines', 'TaxCode', 'Tax Code', 'select', 1, 1, 8, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('location_code', 'lines', 'LocationCode', 'Location Code', 'text', 1, 0, 9, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('costing_code', 'lines', 'CostingCode', 'Cost Center 1', 'select', 1, 0, 10, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('costing_code2', 'lines', 'CostingCode2', 'Cost Center 2', 'select', 1, 0, 11, 1);
                    INSERT OR IGNORE INTO form_fields VALUES ('costing_code3', 'lines', 'CostingCode3', 'Cost Center 3', 'select', 1, 0, 12, 1);
                """)
                try:
                    connection.execute("ALTER TABLE custom_master_data ADD COLUMN is_default INTEGER DEFAULT 0;")
                except Exception:
                    pass
                try:
                    connection.execute("ALTER TABLE uploads ADD COLUMN invoice_payload TEXT;")
                except Exception:
                    pass

                # Migration: ensure new tables exist on older DBs
                try:
                    connection.execute("""CREATE TABLE IF NOT EXISTS wtax_codes (
                        wtax_code TEXT PRIMARY KEY,
                        description TEXT NOT NULL,
                        wtax_rate REAL NOT NULL DEFAULT 0.0,
                        wtax_type TEXT NOT NULL DEFAULT 'G',
                        active INTEGER NOT NULL DEFAULT 1
                    );""")
                    connection.execute("INSERT OR IGNORE INTO wtax_codes VALUES ('C004', 'Contractor / Sub-contractor (TDS 2%)', 2.00, 'G', 1);")
                    connection.execute("INSERT OR IGNORE INTO wtax_codes VALUES ('C010', 'Professional / Technical Services (TDS 10%)', 10.00, 'G', 1);")
                    connection.execute("INSERT OR IGNORE INTO wtax_codes VALUES ('C007', 'Rent of Plant & Machinery (TDS 2%)', 2.00, 'G', 1);")
                    connection.execute("INSERT OR IGNORE INTO wtax_codes VALUES ('C194J', 'Professional / Royalty Fees (TDS 10%)', 10.00, 'G', 1);")
                    connection.execute("INSERT OR IGNORE INTO wtax_codes VALUES ('C194I', 'Rent of Land / Building / Furniture (TDS 10%)', 10.00, 'G', 1);")
                except Exception:
                    pass
                try:
                    connection.execute("""CREATE TABLE IF NOT EXISTS vendor_addresses (
                        vendor_code TEXT NOT NULL,
                        address_code TEXT NOT NULL,
                        address_type TEXT NOT NULL DEFAULT 'ship',
                        address_text TEXT NOT NULL,
                        address2 TEXT,
                        address3 TEXT,
                        block TEXT,
                        building TEXT,
                        street TEXT,
                        street_no TEXT,
                        city TEXT,
                        country TEXT,
                        state TEXT,
                        gst_regn_no TEXT,
                        is_default INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (vendor_code, address_code, address_type)
                    );""")
                except Exception:
                    pass
                
                # Add columns if they don't exist
                for col in ["address2 TEXT", "address3 TEXT", "block TEXT", "building TEXT", "street TEXT", "street_no TEXT", "city TEXT", "country TEXT", "state TEXT", "gst_regn_no TEXT"]:
                    col_name = col.split()[0]
                    try:
                        connection.execute(f"ALTER TABLE vendor_addresses ADD COLUMN {col};")
                    except Exception:
                        pass
                        
                try:
                    connection.execute("""CREATE TABLE IF NOT EXISTS sap_vendor_master (
                        card_code TEXT PRIMARY KEY,
                        group_name TEXT,
                        payment_group TEXT,
                        extra_days INTEGER,
                        currency TEXT,
                        balance REAL,
                        balance_fc REAL
                    );""")
                except Exception:
                    pass
                    
                try:
                    connection.execute("""CREATE TABLE IF NOT EXISTS app_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT
                    );""")
                except Exception:
                    pass

    def get_setting(self, key: str) -> str | None:
        rows = self._execute_sql("SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,))
        return rows[0][0] if rows else None

    def set_setting(self, key: str, value: str) -> None:
        if self.db_engine == "mssql":
            self._execute_sql(
                "UPDATE app_settings SET setting_value = ? WHERE setting_key = ?; IF @@ROWCOUNT = 0 INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?);",
                (value, key, key, value)
            )
        elif self.db_engine == "mysql":
            self._execute_sql(
                "INSERT INTO app_settings (setting_key, setting_value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
                (key, value)
            )
        else:
            self._execute_sql(
                "INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
                (key, value)
            )

    def find_register_vendor(self, supplier_name: str) -> tuple[str, str] | None:
        rows = self._execute_sql(
            "SELECT card_code, card_name FROM sap_register_vendors WHERE LOWER(card_name) LIKE ? LIMIT 1",
            (f"%{supplier_name.strip().casefold()}%",),
        )
        if not rows:
            tokens = [t for t in supplier_name.strip().split() if len(t) > 2]
            if tokens:
                clause = " AND ".join(["LOWER(card_name) LIKE ?"] * len(tokens))
                params = [f"%{t.casefold()}%" for t in tokens]
                rows = self._execute_sql(f"SELECT card_code, card_name FROM sap_register_vendors WHERE {clause} LIMIT 1", params)
        return (rows[0][0], rows[0][1]) if rows else None

    def find_register_item(self, description: str) -> tuple[str, str] | None:
        rows = self._execute_sql(
            "SELECT item_code, item_name FROM sap_register_items WHERE LOWER(item_name) LIKE ? LIMIT 1",
            (f"%{description.strip().casefold()}%",),
        )
        if not rows:
            tokens = [t for t in description.strip().split() if len(t) > 2]
            if len(tokens) >= 2:
                clause = " AND ".join(["LOWER(item_name) LIKE ?"] * min(len(tokens), 3))
                params = [f"%{t.casefold()}%" for t in tokens[:3]]
                rows = self._execute_sql(f"SELECT item_code, item_name FROM sap_register_items WHERE {clause} LIMIT 1", params)
        return (rows[0][0], rows[0][1]) if rows else None

    def vendor_exists_in_register(self, card_code: str) -> bool:
        rows = self._execute_sql("SELECT 1 FROM sap_register_vendors WHERE card_code = ? LIMIT 1", (card_code.strip(),))
        if not rows:
            rows = self._execute_sql("SELECT 1 FROM custom_master_data WHERE category = 'VENDOR' AND code = ? LIMIT 1", (card_code.strip(),))
        return bool(rows)

    def item_exists_in_register(self, item_code: str) -> bool:
        rows = self._execute_sql("SELECT 1 FROM sap_register_items WHERE item_code = ? LIMIT 1", (item_code.strip(),))
        if not rows:
            rows = self._execute_sql("SELECT 1 FROM custom_master_data WHERE category = 'ITEM' AND code = ? LIMIT 1", (item_code.strip(),))
        return bool(rows)

    def save(self, document: InvoiceDocument) -> InvoiceDocument:
        card_code = document.invoice.supplier.sap_card_code if document.invoice.supplier else ""
        inv_num = document.invoice.invoice_header.invoice_number if document.invoice.invoice_header else ""
        status_val = str(document.status)
        payload_val = document.model_dump_json()
        doc_id = str(document.document_id)
        if self.db_engine == "mssql":
            sql = """UPDATE documents SET card_code=?, invoice_number=?, status=?, payload=? WHERE document_id=?;
                     IF @@ROWCOUNT = 0 INSERT INTO documents(document_id, card_code, invoice_number, status, payload) VALUES (?, ?, ?, ?, ?);"""
            self._execute_sql(sql, (card_code, inv_num, status_val, payload_val, doc_id, doc_id, card_code, inv_num, status_val, payload_val))
        elif self.db_engine == "mysql":
            sql = """INSERT INTO documents(document_id, card_code, invoice_number, status, payload)
                     VALUES (?, ?, ?, ?, ?)
                     ON DUPLICATE KEY UPDATE card_code=VALUES(card_code),
                       invoice_number=VALUES(invoice_number), status=VALUES(status), payload=VALUES(payload)"""
            self._execute_sql(sql, (doc_id, card_code, inv_num, status_val, payload_val))
        else:
            sql = """INSERT INTO documents(document_id, card_code, invoice_number, status, payload)
                     VALUES (?, ?, ?, ?, ?)
                     ON CONFLICT(document_id) DO UPDATE SET card_code=excluded.card_code,
                       invoice_number=excluded.invoice_number, status=excluded.status, payload=excluded.payload"""
            self._execute_sql(sql, (doc_id, card_code, inv_num, status_val, payload_val))
        return deepcopy(document)

    def get(self, document_id: UUID) -> InvoiceDocument | None:
        rows = self._execute_sql("SELECT payload FROM documents WHERE document_id = ?", (str(document_id),))
        return InvoiceDocument.model_validate_json(rows[0][0]) if rows else None

    def list(self) -> list[InvoiceDocument]:
        if self.db_engine in ("mysql", "mssql"):
            rows = self._execute_sql("SELECT payload FROM documents ORDER BY document_id DESC")
        else:
            rows = self._execute_sql("SELECT payload FROM documents ORDER BY rowid DESC")
        return [InvoiceDocument.model_validate_json(row[0]) for row in rows]

    def delete(self, document_id: str) -> None:
        self._execute_sql("DELETE FROM documents WHERE document_id = ?", (str(document_id),))

    def duplicate_exists(self, card_code: str, invoice_number: str) -> bool:
        rows = self._execute_sql("SELECT 1 FROM posted_keys WHERE card_code = ? AND invoice_number = ?", (card_code.upper(), invoice_number.strip().upper()))
        return bool(rows)

    def mark_posted(self, card_code: str, invoice_number: str) -> None:
        if self.db_engine == "mssql":
            sql = "IF NOT EXISTS (SELECT 1 FROM posted_keys WHERE card_code=? AND invoice_number=?) INSERT INTO posted_keys(card_code, invoice_number) VALUES (?, ?)"
            self._execute_sql(sql, (card_code.upper(), invoice_number.strip().upper(), card_code.upper(), invoice_number.strip().upper()))
        elif self.db_engine == "mysql":
            sql = "INSERT IGNORE INTO posted_keys(card_code, invoice_number) VALUES (?, ?)"
        else:
            sql = "INSERT OR IGNORE INTO posted_keys(card_code, invoice_number) VALUES (?, ?)"
        self._execute_sql(sql, (card_code.upper(), invoice_number.strip().upper()))

    def record_mapping_correction(
        self,
        supplier_name: str,
        ocr_value: str,
        ai_value: str,
        final_correct_value: str,
        mapping_type: str,
        field_payload: str = "",
    ) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        sql = """
            INSERT INTO mapping_history (
                supplier_name, ocr_value, ai_value, final_correct_value, mapping_type, field_payload, correction_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        self._execute_sql(
            sql,
            (
                supplier_name or "",
                ocr_value.strip() if ocr_value else "",
                ai_value or "",
                final_correct_value.strip() if final_correct_value else "",
                mapping_type.upper(),
                field_payload or "",
                now_str,
            ),
        )

    def get_posted_document_by_invoice(self, card_code: str, invoice_number: str) -> "InvoiceDocument | None":
        """Return the most recently POSTED InvoiceDocument for a given CardCode + InvoiceNumber."""
        order_clause = "ORDER BY rowid DESC LIMIT 1" if self.db_engine == "sqlite" else "ORDER BY document_id DESC LIMIT 1"
        rows = self._execute_sql(
            f"SELECT payload FROM documents WHERE UPPER(card_code) = ? AND UPPER(invoice_number) = ? AND status = 'POSTED' {order_clause}",
            (card_code.strip().upper(), invoice_number.strip().upper()),
        )
        if not rows:
            # Also try SAP_DRAFT_READY / APPROVED as fallback (partially completed)
            rows = self._execute_sql(
                f"SELECT payload FROM documents WHERE UPPER(card_code) = ? AND UPPER(invoice_number) = ? AND status IN ('APPROVED','SAP_DRAFT_READY') {order_clause}",
                (card_code.strip().upper(), invoice_number.strip().upper()),
            )
        return InvoiceDocument.model_validate_json(rows[0][0]) if rows else None


    def get_latest_posted_by_vendor(self, card_code: str) -> "InvoiceDocument | None":
        """Return the most recently POSTED InvoiceDocument for any invoice of a given vendor."""
        order_clause = "ORDER BY rowid DESC LIMIT 1" if self.db_engine == "sqlite" else "ORDER BY document_id DESC LIMIT 1"
        rows = self._execute_sql(
            f"SELECT payload FROM documents WHERE UPPER(card_code) = ? AND status = 'POSTED' {order_clause}",
            (card_code.strip().upper(),),
        )
        if not rows:
            rows = self._execute_sql(
                f"SELECT payload FROM documents WHERE UPPER(card_code) = ? AND status IN ('APPROVED','SAP_DRAFT_READY') {order_clause}",
                (card_code.strip().upper(),),
            )
        return InvoiceDocument.model_validate_json(rows[0][0]) if rows else None

    def save_invoice_snapshot(self, card_code: str, invoice_number: str, payload_json: str) -> None:

        """Store a full invoice payload snapshot keyed by card_code::invoice_number for later recall."""
        key = f"{card_code.strip().upper()}::{invoice_number.strip().upper()}"
        self.record_mapping_correction(
            supplier_name=card_code,
            ocr_value=key,
            ai_value=key,
            final_correct_value=key,
            mapping_type="FULL_INVOICE_SNAPSHOT",
            field_payload=payload_json,
        )

    def get_invoice_snapshot(self, card_code: str, invoice_number: str) -> str | None:
        """Retrieve the most recent full invoice snapshot payload for the given card_code + invoice_number."""
        key = f"{card_code.strip().upper()}::{invoice_number.strip().upper()}"
        rows = self._execute_sql(
            "SELECT field_payload FROM mapping_history WHERE UPPER(ocr_value) = ? AND mapping_type = 'FULL_INVOICE_SNAPSHOT' ORDER BY history_id DESC LIMIT 1",
            (key,),
        )
        return rows[0][0] if rows else None

    def get_historical_mapping(self, ocr_value: str, mapping_type: str, supplier_name: str | None = None) -> str | None:
        if supplier_name:
            rows = self._execute_sql(
                "SELECT final_correct_value FROM mapping_history WHERE LOWER(ocr_value) = ? AND mapping_type = ? AND LOWER(supplier_name) = ? ORDER BY history_id DESC LIMIT 1",
                (ocr_value.strip().casefold(), mapping_type.upper(), supplier_name.strip().casefold()),
            )
            if rows:
                return rows[0][0]
        rows = self._execute_sql(
            "SELECT final_correct_value FROM mapping_history WHERE LOWER(ocr_value) = ? AND mapping_type = ? ORDER BY history_id DESC LIMIT 1",
            (ocr_value.strip().casefold(), mapping_type.upper()),
        )
        return rows[0][0] if rows else None

    def get_mapping_history(self, limit: int = 100) -> list[dict]:
        rows = self._execute_sql(
            "SELECT history_id, supplier_name, ocr_value, ai_value, final_correct_value, mapping_type, field_payload, correction_date FROM mapping_history ORDER BY history_id DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "history_id": r[0],
                "supplier_name": r[1],
                "ocr_value": r[2],
                "ai_value": r[3],
                "final_correct_value": r[4],
                "mapping_type": r[5],
                "field_payload": r[6] or "",
                "correction_date": r[7],
            }
            for r in rows
        ]

    def get_ai_training_stats(self) -> dict:
        total_rows = self._execute_sql("SELECT COUNT(*) FROM mapping_history")
        vendor_rules = self._execute_sql("SELECT COUNT(DISTINCT ocr_value) FROM mapping_history WHERE mapping_type = 'VENDOR'")
        item_rules = self._execute_sql("SELECT COUNT(DISTINCT ocr_value) FROM mapping_history WHERE mapping_type = 'ITEM'")
        gl_rules = self._execute_sql("SELECT COUNT(DISTINCT ocr_value) FROM mapping_history WHERE mapping_type = 'GL_ACCOUNT'")

        return {
            "total_records": total_rows[0][0] if total_rows else 0,
            "vendor_rules": vendor_rules[0][0] if vendor_rules else 0,
            "item_rules": item_rules[0][0] if item_rules else 0,
            "gl_rules": gl_rules[0][0] if gl_rules else 0,
        }

    def get_custom_master_data(self, category: str | None = None) -> list[dict]:
        if category:
            rows = self._execute_sql("SELECT category, code, name, extra_data, is_default FROM custom_master_data WHERE category = ? ORDER BY code", (category,))
        else:
            rows = self._execute_sql("SELECT category, code, name, extra_data, is_default FROM custom_master_data ORDER BY category, code")
        return [{"category": r[0], "code": r[1], "name": r[2], "extra_data": r[3], "is_default": bool(r[4]) if len(r) > 4 else False} for r in rows]

    def upsert_custom_master_data(self, category: str, code: str, name: str, extra_data: str = "", is_default: bool = False) -> None:
        if is_default:
            self._execute_sql("UPDATE custom_master_data SET is_default = 0 WHERE category = ?", (category,))
        if self.db_engine == "mssql":
            sql = "UPDATE custom_master_data SET name=?, extra_data=?, is_default=? WHERE category=? AND code=?; IF @@ROWCOUNT = 0 INSERT INTO custom_master_data (category, code, name, extra_data, is_default) VALUES (?, ?, ?, ?, ?);"
            self._execute_sql(sql, (name.strip(), extra_data.strip(), 1 if is_default else 0, category, code.strip(), category, code.strip(), name.strip(), extra_data.strip(), 1 if is_default else 0))
        elif self.db_engine == "mysql":
            sql = "INSERT INTO custom_master_data (category, code, name, extra_data, is_default) VALUES (?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE name=VALUES(name), extra_data=VALUES(extra_data), is_default=VALUES(is_default)"
            self._execute_sql(sql, (category, code.strip(), name.strip(), extra_data.strip(), 1 if is_default else 0))
        else:
            sql = "INSERT INTO custom_master_data (category, code, name, extra_data, is_default) VALUES (?, ?, ?, ?, ?) ON CONFLICT(category, code) DO UPDATE SET name=excluded.name, extra_data=excluded.extra_data, is_default=excluded.is_default"
            self._execute_sql(sql, (category, code.strip(), name.strip(), extra_data.strip(), 1 if is_default else 0))

    def delete_custom_master_data(self, category: str, code: str) -> None:
        self._execute_sql("DELETE FROM custom_master_data WHERE category = ? AND code = ?", (category.strip(), code.strip()))

    def delete_custom_master_category(self, category: str) -> None:
        self._execute_sql("DELETE FROM custom_master_data WHERE category = ?", (category.strip(),))

    def get_users(self) -> list[dict]:
        rows = self._execute_sql("SELECT username, password, role, created_at FROM app_users ORDER BY username")
        return [{"username": r[0], "password": r[1], "role": r[2], "created_at": r[3]} for r in rows]

    def upsert_user(self, username: str, password: str, role: str) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        if self.db_engine == "mssql":
            sql = "UPDATE app_users SET password=?, role=? WHERE username=?; IF @@ROWCOUNT = 0 INSERT INTO app_users (username, password, role, created_at) VALUES (?, ?, ?, ?);"
            self._execute_sql(sql, (password.strip(), role.strip().lower(), username.strip(), username.strip(), password.strip(), role.strip().lower(), now_str))
        elif self.db_engine == "mysql":
            sql = "INSERT INTO app_users (username, password, role, created_at) VALUES (?, ?, ?, ?) ON DUPLICATE KEY UPDATE password=VALUES(password), role=VALUES(role)"
            self._execute_sql(sql, (username.strip(), password.strip(), role.strip().lower(), now_str))
        else:
            sql = "INSERT INTO app_users (username, password, role, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET password=excluded.password, role=excluded.role"
            self._execute_sql(sql, (username.strip(), password.strip(), role.strip().lower(), now_str))

    def delete_user(self, username: str) -> None:
        self._execute_sql("DELETE FROM app_users WHERE username = ?", (username.strip(),))

    def verify_and_get_user(self, username: str, password: str, role: str | None = None) -> dict | None:
        if role:
            rows = self._execute_sql(
                "SELECT username, role FROM app_users WHERE LOWER(username) = ? AND password = ? AND LOWER(role) = ?",
                (username.strip().casefold(), password.strip(), role.strip().casefold()),
            )
        else:
            rows = []

        if not rows:
            rows = self._execute_sql(
                "SELECT username, role FROM app_users WHERE LOWER(username) = ? AND password = ?",
                (username.strip().casefold(), password.strip()),
            )

        if rows:
            return {"username": rows[0][0], "role": rows[0][1]}
        return None

    def verify_user(self, username: str, password: str, role: str) -> bool:
        return self.verify_and_get_user(username, password, role) is not None

    def get_latest_upload_path_by_filename(self, filename: str) -> str | None:
        if self.db_engine == "mysql":
            rows = self._execute_sql("SELECT stored_path FROM uploads WHERE filename = ? ORDER BY created_at DESC LIMIT 1", (filename,))
        else:
            rows = self._execute_sql("SELECT stored_path FROM uploads WHERE filename = ? ORDER BY rowid DESC LIMIT 1", (filename,))
        return rows[0][0] if rows else None

    def get_form_fields(self, section: str | None = None) -> list[dict]:
        if section:
            rows = self._execute_sql("SELECT field_id, section, sap_param_name, label, field_type, enabled, required, sort_order, visible FROM form_fields WHERE section = ? ORDER BY sort_order", (section,))
        else:
            rows = self._execute_sql("SELECT field_id, section, sap_param_name, label, field_type, enabled, required, sort_order, visible FROM form_fields ORDER BY section, sort_order")
        return [
            {
                "field_id": r[0],
                "section": r[1],
                "sap_param_name": r[2],
                "label": r[3],
                "field_type": r[4],
                "enabled": bool(r[5]),
                "required": bool(r[6]),
                "sort_order": r[7],
                "visible": bool(r[8]) if len(r) > 8 else True,
            }
            for r in rows
        ]

    def upsert_form_field(self, field_id: str, section: str, sap_param_name: str, label: str, field_type: str = "text", enabled: bool = True, required: bool = False, sort_order: int = 0, visible: bool = True) -> None:
        if self.db_engine == "mssql":
            sql = """UPDATE form_fields SET section=?, sap_param_name=?, label=?, field_type=?, enabled=?, required=?, sort_order=?, visible=? WHERE field_id=?; 
                     IF @@ROWCOUNT = 0 INSERT INTO form_fields (field_id, section, sap_param_name, label, field_type, enabled, required, sort_order, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);"""
            self._execute_sql(sql, (section.strip(), sap_param_name.strip(), label.strip(), field_type.strip(), 1 if enabled else 0, 1 if required else 0, sort_order, 1 if visible else 0, field_id.strip(), field_id.strip(), section.strip(), sap_param_name.strip(), label.strip(), field_type.strip(), 1 if enabled else 0, 1 if required else 0, sort_order, 1 if visible else 0))
        elif self.db_engine == "mysql":
            sql = """
                INSERT INTO form_fields (field_id, section, sap_param_name, label, field_type, enabled, required, sort_order, visible)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    section=VALUES(section),
                    sap_param_name=VALUES(sap_param_name),
                    label=VALUES(label),
                    field_type=VALUES(field_type),
                    enabled=VALUES(enabled),
                    required=VALUES(required),
                    sort_order=VALUES(sort_order),
                    visible=VALUES(visible)
            """
            self._execute_sql(sql, (field_id.strip(), section.strip(), sap_param_name.strip(), label.strip(), field_type.strip(), 1 if enabled else 0, 1 if required else 0, sort_order, 1 if visible else 0))
        else:
            sql = """
                INSERT INTO form_fields (field_id, section, sap_param_name, label, field_type, enabled, required, sort_order, visible)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(field_id) DO UPDATE SET
                    section=excluded.section,
                    sap_param_name=excluded.sap_param_name,
                    label=excluded.label,
                    field_type=excluded.field_type,
                    enabled=excluded.enabled,
                    required=excluded.required,
                    sort_order=excluded.sort_order,
                    visible=excluded.visible
            """
            self._execute_sql(sql, (field_id.strip(), section.strip(), sap_param_name.strip(), label.strip(), field_type.strip(), 1 if enabled else 0, 1 if required else 0, sort_order, 1 if visible else 0))

    def delete_form_field(self, field_id: str) -> None:
        self._execute_sql("DELETE FROM form_fields WHERE field_id = ?", (field_id.strip(),))

    # ── WHT / TDS Code Master ──────────────────────────────────────────────────

    def get_wtax_codes(self, active_only: bool = False) -> list[dict]:
        if active_only:
            rows = self._execute_sql("SELECT wtax_code, description, wtax_rate, wtax_type, active FROM wtax_codes WHERE active = 1 ORDER BY wtax_code")
        else:
            rows = self._execute_sql("SELECT wtax_code, description, wtax_rate, wtax_type, active FROM wtax_codes ORDER BY wtax_code")
        return [{"wtax_code": r[0], "description": r[1], "wtax_rate": float(r[2]), "wtax_type": r[3], "active": bool(r[4])} for r in rows]

    def upsert_wtax_code(self, wtax_code: str, description: str, wtax_rate: float, wtax_type: str = "G", active: bool = True) -> None:
        if self.db_engine == "mssql":
            sql = "UPDATE wtax_codes SET description=?, wtax_rate=?, wtax_type=?, active=? WHERE wtax_code=?; IF @@ROWCOUNT = 0 INSERT INTO wtax_codes (wtax_code, description, wtax_rate, wtax_type, active) VALUES (?, ?, ?, ?, ?);"
            self._execute_sql(sql, (description.strip(), float(wtax_rate), wtax_type.strip().upper()[:1] or "G", 1 if active else 0, wtax_code.strip().upper(), wtax_code.strip().upper(), description.strip(), float(wtax_rate), wtax_type.strip().upper()[:1] or "G", 1 if active else 0))
        elif self.db_engine == "mysql":
            sql = "INSERT INTO wtax_codes (wtax_code, description, wtax_rate, wtax_type, active) VALUES (?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE description=VALUES(description), wtax_rate=VALUES(wtax_rate), wtax_type=VALUES(wtax_type), active=VALUES(active)"
            self._execute_sql(sql, (wtax_code.strip().upper(), description.strip(), float(wtax_rate), wtax_type.strip().upper()[:1] or "G", 1 if active else 0))
        else:
            sql = "INSERT INTO wtax_codes (wtax_code, description, wtax_rate, wtax_type, active) VALUES (?, ?, ?, ?, ?) ON CONFLICT(wtax_code) DO UPDATE SET description=excluded.description, wtax_rate=excluded.wtax_rate, wtax_type=excluded.wtax_type, active=excluded.active"
            self._execute_sql(sql, (wtax_code.strip().upper(), description.strip(), float(wtax_rate), wtax_type.strip().upper()[:1] or "G", 1 if active else 0))

    def delete_wtax_code(self, wtax_code: str) -> None:
        self._execute_sql("DELETE FROM wtax_codes WHERE wtax_code = ?", (wtax_code.strip().upper(),))

    # ── Vendor Addresses Master ────────────────────────────────────────────────

    def get_vendor_addresses(self, vendor_code: str | None = None, address_type: str | None = None) -> list[dict]:
        if vendor_code and address_type:
            rows = self._execute_sql("SELECT vendor_code, address_code, address_type, address_text, address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, is_default FROM vendor_addresses WHERE vendor_code = ? AND address_type = ? ORDER BY is_default DESC, address_code", (vendor_code.strip(), address_type.strip()))
        elif vendor_code:
            rows = self._execute_sql("SELECT vendor_code, address_code, address_type, address_text, address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, is_default FROM vendor_addresses WHERE vendor_code = ? ORDER BY address_type, is_default DESC, address_code", (vendor_code.strip(),))
        else:
            rows = self._execute_sql("SELECT vendor_code, address_code, address_type, address_text, address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, is_default FROM vendor_addresses ORDER BY vendor_code, address_type, is_default DESC, address_code")
        
        return [
            {
                "vendor_code": r[0], "address_code": r[1], "address_type": r[2], "address_text": r[3],
                "address2": r[4] or "", "address3": r[5] or "", "block": r[6] or "", "building": r[7] or "",
                "street": r[8] or "", "street_no": r[9] or "", "city": r[10] or "", "country": r[11] or "",
                "state": r[12] or "", "gst_regn_no": r[13] or "", "is_default": bool(r[14])
            } for r in rows
        ]

    def upsert_vendor_address(self, vendor_code: str, address_code: str, address_type: str, address_text: str, is_default: bool = False, address2="", address3="", block="", building="", street="", street_no="", city="", country="", state="", gst_regn_no="") -> None:
        addr_type = address_type.strip().lower()[:4]  # 'ship' or 'pay'
        if is_default:
            self._execute_sql("UPDATE vendor_addresses SET is_default = 0 WHERE vendor_code = ? AND address_type = ?", (vendor_code.strip(), addr_type))
        if self.db_engine == "mssql":
            sql = "UPDATE vendor_addresses SET address_text=?, address2=?, address3=?, block=?, building=?, street=?, street_no=?, city=?, country=?, state=?, gst_regn_no=?, is_default=? WHERE vendor_code=? AND address_code=? AND address_type=?; IF @@ROWCOUNT = 0 INSERT INTO vendor_addresses (vendor_code, address_code, address_type, address_text, address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, is_default) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);"
            self._execute_sql(sql, (address_text.strip(), address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, 1 if is_default else 0, vendor_code.strip(), address_code.strip(), addr_type, vendor_code.strip(), address_code.strip(), addr_type, address_text.strip(), address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, 1 if is_default else 0))
        elif self.db_engine == "mysql":
            sql = "INSERT INTO vendor_addresses (vendor_code, address_code, address_type, address_text, address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, is_default) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE address_text=VALUES(address_text), address2=VALUES(address2), address3=VALUES(address3), block=VALUES(block), building=VALUES(building), street=VALUES(street), street_no=VALUES(street_no), city=VALUES(city), country=VALUES(country), state=VALUES(state), gst_regn_no=VALUES(gst_regn_no), is_default=VALUES(is_default)"
            self._execute_sql(sql, (vendor_code.strip(), address_code.strip(), addr_type, address_text.strip(), address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, 1 if is_default else 0))
        else:
            sql = "INSERT INTO vendor_addresses (vendor_code, address_code, address_type, address_text, address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, is_default) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_code, address_code, address_type) DO UPDATE SET address_text=excluded.address_text, address2=excluded.address2, address3=excluded.address3, block=excluded.block, building=excluded.building, street=excluded.street, street_no=excluded.street_no, city=excluded.city, country=excluded.country, state=excluded.state, gst_regn_no=excluded.gst_regn_no, is_default=excluded.is_default"
            self._execute_sql(sql, (vendor_code.strip(), address_code.strip(), addr_type, address_text.strip(), address2, address3, block, building, street, street_no, city, country, state, gst_regn_no, 1 if is_default else 0))

    def delete_vendor_address(self, vendor_code: str, address_code: str, address_type: str) -> None:
        self._execute_sql("DELETE FROM vendor_addresses WHERE vendor_code = ? AND address_code = ? AND address_type = ?", (vendor_code.strip(), address_code.strip(), address_type.strip().lower()[:4]))

    # ── SAP Vendor Master ──────────────────────────────────────────────────────

    def get_sap_vendor_master(self, card_code: str | None = None) -> list[dict]:
        if card_code:
            rows = self._execute_sql("SELECT card_code, group_name, payment_group, extra_days, currency, balance, balance_fc FROM sap_vendor_master WHERE card_code = ?", (card_code.strip(),))
        else:
            rows = self._execute_sql("SELECT card_code, group_name, payment_group, extra_days, currency, balance, balance_fc FROM sap_vendor_master ORDER BY card_code")
        return [
            {
                "card_code": r[0], "group_name": r[1] or "", "payment_group": r[2] or "", 
                "extra_days": r[3] or 0, "currency": r[4] or "", 
                "balance": float(r[5] or 0), "balance_fc": float(r[6] or 0)
            } for r in rows
        ]

    def upsert_sap_vendor_master(self, card_code: str, group_name: str="", payment_group: str="", extra_days: int=0, currency: str="", balance: float=0.0, balance_fc: float=0.0) -> None:
        if self.db_engine == "mssql":
            sql = "UPDATE sap_vendor_master SET group_name=?, payment_group=?, extra_days=?, currency=?, balance=?, balance_fc=? WHERE card_code=?; IF @@ROWCOUNT = 0 INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc) VALUES (?, ?, ?, ?, ?, ?, ?);"
            self._execute_sql(sql, (group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc), card_code.strip(), card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc)))
        elif self.db_engine == "mysql":
            sql = "INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc) VALUES (?, ?, ?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE group_name=VALUES(group_name), payment_group=VALUES(payment_group), extra_days=VALUES(extra_days), currency=VALUES(currency), balance=VALUES(balance), balance_fc=VALUES(balance_fc)"
            self._execute_sql(sql, (card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc)))
        else:
            sql = "INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(card_code) DO UPDATE SET group_name=excluded.group_name, payment_group=excluded.payment_group, extra_days=excluded.extra_days, currency=excluded.currency, balance=excluded.balance, balance_fc=excluded.balance_fc"
            self._execute_sql(sql, (card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc)))

    def get_open_pos(self, vendor_code: str) -> list[dict]:
        sql = "SELECT doc_entry, doc_num, vendor_code, doc_date, total_amount, lines_payload FROM open_po WHERE vendor_code = ?"
        rows = self._execute_sql(sql, (vendor_code,))
        return [
            {
                "doc_entry": r[0],
                "doc_num": r[1],
                "vendor_code": r[2],
                "doc_date": r[3],
                "total_amount": float(r[4]) if r[4] else 0.0,
                "lines_payload": r[5]
            } for r in rows
        ]

    def get_open_grns(self, vendor_code: str) -> list[dict]:
        sql = "SELECT doc_entry, doc_num, vendor_code, doc_date, total_amount, lines_payload FROM open_grn WHERE vendor_code = ?"
        rows = self._execute_sql(sql, (vendor_code,))
        return [
            {
                "doc_entry": r[0],
                "doc_num": r[1],
                "vendor_code": r[2],
                "doc_date": r[3],
                "total_amount": float(r[4]) if r[4] else 0.0,
                "lines_payload": r[5]
            } for r in rows
        ]

    def replace_open_documents(self, doc_type: str, docs: list[dict]) -> None:
        table_name = "open_po" if doc_type == "PO" else "open_grn"
        self._execute_sql(f"DELETE FROM {table_name}")
        for doc in docs:
            self._execute_sql(f"INSERT INTO {table_name} (doc_entry, doc_num, vendor_code, doc_date, total_amount, lines_payload) VALUES (?, ?, ?, ?, ?, ?)",
                (doc.get('doc_entry'), doc.get('doc_num'), doc.get('vendor_code'), doc.get('doc_date'), doc.get('total_amount'), doc.get('lines_payload')))

    def get_all_open_documents(self) -> list[dict]:
        sql = """
            SELECT doc_entry, doc_num, vendor_code, doc_date, total_amount, lines_payload, 'PO' as doc_type FROM open_po
            UNION ALL
            SELECT doc_entry, doc_num, vendor_code, doc_date, total_amount, lines_payload, 'GRN' as doc_type FROM open_grn
        """
        rows = self._execute_sql(sql)
        return [
            {
                "doc_entry": r[0],
                "doc_num": r[1],
                "vendor_code": r[2],
                "doc_date": r[3],
                "total_amount": float(r[4]) if r[4] else 0.0,
                "lines_payload": r[5],
                "doc_type": r[6]
            } for r in rows
        ]

    # ── Validation Rules ─────────────────────────────────────────────────────

    def get_validation_rules(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT id, rule_name, target_field, condition, condition_value, error_message, is_active FROM validation_rules"
        if active_only:
            sql += " WHERE is_active = 1"
        rows = self._execute_sql(sql)
        return [
            {
                "id": r[0],
                "rule_name": r[1],
                "target_field": r[2],
                "condition": r[3],
                "condition_value": r[4],
                "error_message": r[5],
                "is_active": bool(r[6])
            } for r in rows
        ]

    def upsert_validation_rule(self, id: int | None, rule_name: str, target_field: str, condition: str, condition_value: str, error_message: str, is_active: bool) -> dict:
        if id:
            self._execute_sql(
                "UPDATE validation_rules SET rule_name=?, target_field=?, condition=?, condition_value=?, error_message=?, is_active=? WHERE id=?",
                (rule_name, target_field, condition, condition_value, error_message, 1 if is_active else 0, id)
            )
        else:
            if self.db_engine == "mssql":
                self._execute_sql(
                    "INSERT INTO validation_rules (rule_name, target_field, condition, condition_value, error_message, is_active) VALUES (?, ?, ?, ?, ?, ?)",
                    (rule_name, target_field, condition, condition_value, error_message, 1 if is_active else 0)
                )
                rows = self._execute_sql("SELECT SCOPE_IDENTITY()")
                id = int(rows[0][0]) if rows else None
            elif self.db_engine == "mysql":
                self._execute_sql(
                    "INSERT INTO validation_rules (rule_name, target_field, condition, condition_value, error_message, is_active) VALUES (?, ?, ?, ?, ?, ?)",
                    (rule_name, target_field, condition, condition_value, error_message, 1 if is_active else 0)
                )
                rows = self._execute_sql("SELECT LAST_INSERT_ID()")
                id = int(rows[0][0]) if rows else None
            else:
                with sqlite3.connect(self.database_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO validation_rules (rule_name, target_field, condition, condition_value, error_message, is_active) VALUES (?, ?, ?, ?, ?, ?)",
                        (rule_name, target_field, condition, condition_value, error_message, 1 if is_active else 0)
                    )
                    id = cursor.lastrowid
                    conn.commit()
                    
        return {
            "id": id,
            "rule_name": rule_name,
            "target_field": target_field,
            "condition": condition,
            "condition_value": condition_value,
            "error_message": error_message,
            "is_active": is_active
        }

    def delete_validation_rule(self, id: int) -> None:
        self._execute_sql("DELETE FROM validation_rules WHERE id = ?", (id,))


# Backward compatibility alias
SQLiteRepository = DatabaseRepository
