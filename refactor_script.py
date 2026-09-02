import os

main_file = r'd:\Documents\AP Invoice OCR\app\main.py'
doc_router_file = r'd:\Documents\AP Invoice OCR\app\routers\documents.py'

# Read main.py
with open(main_file, 'r', encoding='utf-8') as f:
    main_content = f.read()

# Find the start of map_document
start_idx = main_content.find('@app.post("/documents/{document_id}/map"')
if start_idx == -1:
    print("Could not find start index")
    exit(1)

# Find the end of extract_region
extract_def = 'async def extract_region'
extract_idx = main_content.find(extract_def, start_idx)
# find the end of the function (the last line is raise HTTPException(status_code=500, detail=str(exc)))
end_idx = main_content.find('raise HTTPException(status_code=500, detail=str(exc))', extract_idx)
if end_idx == -1:
    print("Could not find end index")
    exit(1)
end_idx += len('raise HTTPException(status_code=500, detail=str(exc))')

# Extract block
extracted_block = main_content[start_idx:end_idx]

# Replace @app. with @router.
extracted_block = extracted_block.replace('@app.post', '@router.post')
extracted_block = extracted_block.replace('@app.get', '@router.get')

# Remove from main.py
new_main_content = main_content[:start_idx] + main_content[end_idx:]
with open(main_file, 'w', encoding='utf-8') as f:
    f.write(new_main_content)

# Update documents.py
with open(doc_router_file, 'r', encoding='utf-8') as f:
    doc_content = f.read()

# Add imports
imports_to_add = """
from fastapi import UploadFile, File, Form
from app.models import DocumentStatus, ApprovalRequest
from app.services import InvoiceService
from app.sap_client import post_draft, SapClientError
import base64

service = InvoiceService(repository)
"""
doc_content = doc_content.replace('from uuid import UUID\n', 'from uuid import UUID\n' + imports_to_add)

# Append block
doc_content += "\n\n" + extracted_block + "\n"

with open(doc_router_file, 'w', encoding='utf-8') as f:
    f.write(doc_content)

print("Refactoring complete.")
