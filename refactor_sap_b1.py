import re

file_path = "app/erp/sap_b1.py"
with open(file_path, "r") as f:
    content = f.read()

# Add imports
imports = """
from .base import ERPClient, ERPClientError

class SapB1Client(ERPClient):
"""
content = re.sub(r'class SapClientError\(RuntimeError\):\n    pass', '', content)
content = content.replace("SapClientError", "ERPClientError")

# Make functions into methods
# Indent everything after imports
lines = content.split('\n')
new_lines = []
in_class = False

for i, line in enumerate(lines):
    if line.startswith("def _clean_payload"):
        new_lines.append(imports)
        in_class = True
    
    if in_class:
        if line.startswith("def "):
            # Add self
            line = line.replace("def ", "    def ").replace("(", "(self, ", 1).replace("(self, )", "(self)")
        elif line.strip() or not line.strip() and in_class:
            if line:
                line = "    " + line
        new_lines.append(line)
    else:
        new_lines.append(line)

content = "\n".join(new_lines)

# Fix internal calls
content = re.sub(r'(?<!def )_clean_payload\(', 'self._clean_payload(', content)
content = re.sub(r'(?<!def )login_and_get_session\(', 'self._login_and_get_session(', content)
content = re.sub(r'(?<!def )logout\(', 'self._logout(', content)
content = re.sub(r'(?<!def )upload_attachment_to_sap\(', 'self._upload_attachment_to_sap(', content)
content = re.sub(r'(?<!def )_fetch_all_odata_pages\(', 'self._fetch_all_odata_pages(', content)
content = re.sub(r'(?<!def )_fetch_sql_query_from_sap\(', 'self._fetch_sql_query_from_sap(', content)

content = content.replace("def login_and_get_session(", "def _login_and_get_session(")
content = content.replace("def logout(", "def _logout(")
content = content.replace("def upload_attachment_to_sap(", "def _upload_attachment_to_sap(")
content = content.replace("def get_all_open_documents_from_sap(", "def get_all_open_documents(")
content = content.replace("def sync_all_master_data_from_sap(", "def sync_all_master_data(")

with open(file_path, "w") as f:
    f.write(content)
