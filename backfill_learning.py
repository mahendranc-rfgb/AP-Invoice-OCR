import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from app.main import repository, service
from app.models import DocumentStatus

docs = repository.list()
count = 0
for doc in docs:
    if doc.invoice.supplier.sap_card_code and doc.invoice.lines:
        service.learn_from_document(doc)
        count += 1

stats = repository.get_ai_training_stats()
print(f"Successfully backfilled mappings from {count} documents.")
print(f"Current AI Training Stats: {stats}")
