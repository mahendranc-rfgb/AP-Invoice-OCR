import re

repo_path = r'd:\Documents\AP Invoice OCR\app\repository.py'
with open(repo_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Update get_sap_vendor_master
get_old = """    def get_sap_vendor_master(self, card_code: str | None = None) -> list[dict]:
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
        ]"""
get_new = """    def get_sap_vendor_master(self, card_code: str | None = None) -> list[dict]:
        if card_code:
            rows = self._execute_sql("SELECT card_code, group_name, payment_group, extra_days, currency, balance, balance_fc, gstin FROM sap_vendor_master WHERE card_code = ?", (card_code.strip(),))
        else:
            rows = self._execute_sql("SELECT card_code, group_name, payment_group, extra_days, currency, balance, balance_fc, gstin FROM sap_vendor_master ORDER BY card_code")
        return [
            {
                "card_code": r[0], "group_name": r[1] or "", "payment_group": r[2] or "", 
                "extra_days": r[3] or 0, "currency": r[4] or "", 
                "balance": float(r[5] or 0), "balance_fc": float(r[6] or 0),
                "gstin": r[7] or ""
            } for r in rows
        ]"""

# Update upsert_sap_vendor_master
up_old = """    def upsert_sap_vendor_master(self, card_code: str, group_name: str="", payment_group: str="", extra_days: int=0, currency: str="", balance: float=0.0, balance_fc: float=0.0) -> None:
        if self.db_engine == "mssql":
            sql = "UPDATE sap_vendor_master SET group_name=?, payment_group=?, extra_days=?, currency=?, balance=?, balance_fc=? WHERE card_code=?; IF @@ROWCOUNT = 0 INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc) VALUES (?, ?, ?, ?, ?, ?, ?);"
            self._execute_sql(sql, (group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc), card_code.strip(), card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc)))
        elif self.db_engine == "mysql":
            sql = "INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc) VALUES (?, ?, ?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE group_name=VALUES(group_name), payment_group=VALUES(payment_group), extra_days=VALUES(extra_days), currency=VALUES(currency), balance=VALUES(balance), balance_fc=VALUES(balance_fc)"
            self._execute_sql(sql, (card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc)))
        else:
            sql = "INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(card_code) DO UPDATE SET group_name=excluded.group_name, payment_group=excluded.payment_group, extra_days=excluded.extra_days, currency=excluded.currency, balance=excluded.balance, balance_fc=excluded.balance_fc"
            self._execute_sql(sql, (card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc)))"""

up_new = """    def upsert_sap_vendor_master(self, card_code: str, group_name: str="", payment_group: str="", extra_days: int=0, currency: str="", balance: float=0.0, balance_fc: float=0.0, gstin: str="") -> None:
        if self.db_engine == "mssql":
            sql = "UPDATE sap_vendor_master SET group_name=?, payment_group=?, extra_days=?, currency=?, balance=?, balance_fc=?, gstin=? WHERE card_code=?; IF @@ROWCOUNT = 0 INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc, gstin) VALUES (?, ?, ?, ?, ?, ?, ?, ?);"
            self._execute_sql(sql, (group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc), gstin.strip(), card_code.strip(), card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc), gstin.strip()))
        elif self.db_engine == "mysql":
            sql = "INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc, gstin) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE group_name=VALUES(group_name), payment_group=VALUES(payment_group), extra_days=VALUES(extra_days), currency=VALUES(currency), balance=VALUES(balance), balance_fc=VALUES(balance_fc), gstin=VALUES(gstin)"
            self._execute_sql(sql, (card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc), gstin.strip()))
        else:
            sql = "INSERT INTO sap_vendor_master (card_code, group_name, payment_group, extra_days, currency, balance, balance_fc, gstin) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(card_code) DO UPDATE SET group_name=excluded.group_name, payment_group=excluded.payment_group, extra_days=excluded.extra_days, currency=excluded.currency, balance=excluded.balance, balance_fc=excluded.balance_fc, gstin=excluded.gstin"
            self._execute_sql(sql, (card_code.strip(), group_name.strip(), payment_group.strip(), extra_days, currency.strip(), float(balance), float(balance_fc), gstin.strip()))"""

content = content.replace(get_old, get_new)
content = content.replace(up_old, up_new)

with open(repo_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated repository.py")
