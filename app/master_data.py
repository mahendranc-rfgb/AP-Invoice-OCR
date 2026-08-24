from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.repository import SQLiteRepository


@dataclass(frozen=True)
class VendorMatch:
    card_code: str
    card_name: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class ItemMatch:
    item_code: str
    item_name: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class Vendor:
    card_code: str
    card_name: str
    gstin: str | None
    active: bool = True


@dataclass(frozen=True)
class Item:
    item_code: str
    item_name: str
    active: bool = True


def normalize_text(val: str) -> str:
    text = val.casefold()
    text = re.sub(r"\bprivate\b", "pvt", text)
    text = re.sub(r"\blimited\b", "ltd", text)
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


class DemoSapMasterDataGateway:
    """Multi-level SAP Master Data gateway with exact, historical, and similarity matching."""

    vendors = [
        Vendor("V00101", "V.K.V. INDUSTRIES", "33AHYPS1047C1Z9"),
        Vendor("V01000", "MURUGAN - IDLY PLATE HOLE PUNCHING WORK", "33MURUGAN1234Z0"),
        Vendor("V10001", "ABC Chemicals Pvt Ltd", "33XXXXXXXXXXXXXX"),
        Vendor("V10002", "Global Pharma Supplies", "27AAAAA0000A1Z5"),
    ]
    items = [
        Item("RM-1001", "Raw Material A"),
        Item("RM000245", "Paracetamol Powder USP Grade"),
        Item("RM000246", "Lactose Monohydrate"),
    ]
    warehouses = {"WH01", "WH02"}
    tax_codes = {}

    cost_centers1 = []

    cost_centers2 = []

    cost_centers3 = []

    def __init__(self, repository: SQLiteRepository | None = None) -> None:
        self.repository = repository
        if self.repository:
            # Dynamically load all vendors from custom_master_data
            custom_data = self.repository.get_custom_master_data()
            db_vendors = [v for v in custom_data if v["category"] == "vendors"]
            for v in db_vendors:
                extra = {}
                try:
                    import json
                    if v.get("extra_data"):
                        extra = json.loads(v["extra_data"])
                except Exception:
                    pass
                gstin = extra.get("vat_reg_num") or extra.get("gstin")
                # Add to self.vendors, avoiding duplicates
                if not any(existing.card_code == v["code"] for existing in self.vendors):
                    self.vendors.append(Vendor(v["code"], v["name"], gstin))

    def find_vendor_advanced(self, supplier_name: str, gstin: str | None = None) -> VendorMatch | None:
        print(f"DEBUG find_vendor_advanced: supplier_name={repr(supplier_name)}, gstin={repr(gstin)}")
        # Level 1: Historical Mapping Lookup (Human Correction takes precedence)
        if self.repository:
            hist_card_code = self.repository.get_historical_mapping(supplier_name, "VENDOR", supplier_name)
            print(f"DEBUG hist_card_code returned from DB: {hist_card_code}")
            if hist_card_code:
                match = next((v for v in self.vendors if v.card_code == hist_card_code), None)
                return VendorMatch(
                    card_code=hist_card_code,
                    card_name=match.card_name if match else hist_card_code,
                    confidence=98.5,
                    reason="Level 1 — Supplier historical correction memory",
                )

        # Level 2: GSTIN Exact Match
        if gstin:
            for vendor in self.vendors:
                if vendor.gstin and vendor.gstin.strip().upper() == gstin.strip().upper():
                    return VendorMatch(
                        card_code=vendor.card_code,
                        card_name=vendor.card_name,
                        confidence=99.8,
                        reason="Level 2 — GSTIN exact match",
                    )

        # Level 3: SAP AP Invoice Register Dump Search
        if self.repository:
            reg_vendor = self.repository.find_register_vendor(supplier_name)
            if reg_vendor:
                return VendorMatch(
                    card_code=reg_vendor[0],
                    card_name=reg_vendor[1],
                    confidence=96.0,
                    reason="Level 3 — SAP AP Invoice Register dump match",
                )

        # Level 4: Normalized Text Similarity
        norm_input = normalize_text(supplier_name)
        best_vendor = None
        highest_ratio = 0.0

        for vendor in self.vendors:
            norm_name = normalize_text(vendor.card_name)
            ratio = SequenceMatcher(None, norm_input, norm_name).ratio()
            if ratio > highest_ratio:
                highest_ratio = ratio
                best_vendor = vendor

        if best_vendor and highest_ratio >= 0.50:
            conf = round(highest_ratio * 100, 1)
            reason = "Level 1 — Exact name match" if highest_ratio > 0.95 else f"Level 4 — Text similarity ({round(highest_ratio*100)}%)"
            return VendorMatch(card_code=best_vendor.card_code, card_name=best_vendor.card_name, confidence=conf, reason=reason)

        return None

    def find_vendor(self, supplier_name: str, gstin: str | None) -> Vendor | None:
        match = self.find_vendor_advanced(supplier_name, gstin)
        if match:
            return Vendor(card_code=match.card_code, card_name=match.card_name, gstin=gstin)
        return None

    def find_item_advanced(self, description: str, supplier_item_code: str | None = None, supplier_name: str | None = None) -> ItemMatch | None:
        # Level 1: Supplier Item Code Direct Match
        if supplier_item_code:
            for item in self.items:
                if item.item_code.strip().upper() == supplier_item_code.strip().upper():
                    return ItemMatch(item_code=item.item_code, item_name=item.item_name, confidence=99.0, reason="Level 1 — Item Code exact match")

        # Level 2: Historical Mapping Lookup
        if self.repository:
            lookup_key = supplier_item_code or description
            hist_item_code = self.repository.get_historical_mapping(lookup_key, "ITEM", supplier_name)
            if hist_item_code:
                item = next((i for i in self.items if i.item_code == hist_item_code), None)
                if item:
                    return ItemMatch(item_code=item.item_code, item_name=item.item_name, confidence=97.5, reason="Level 2 — Supplier historical mapping memory")

        # Level 3: SAP AP Invoice Register Dump Search
        if self.repository:
            reg_item = self.repository.find_register_item(description)
            if reg_item:
                return ItemMatch(
                    item_code=reg_item[0],
                    item_name=reg_item[1],
                    confidence=95.0,
                    reason="Level 3 — SAP AP Invoice Register dump match",
                )

        # Level 4: Semantic/Text Similarity Match
        norm_desc = normalize_text(description)
        best_item = None
        highest_ratio = 0.0

        for item in self.items:
            norm_name = normalize_text(item.item_name)
            ratio = SequenceMatcher(None, norm_desc, norm_name).ratio()
            if ratio > highest_ratio:
                highest_ratio = ratio
                best_item = item

        if best_item and highest_ratio >= 0.40:
            conf = round(highest_ratio * 100, 1)
            reason = "Level 1 — Description exact match" if highest_ratio > 0.95 else f"Level 3 — Text similarity ({round(highest_ratio*100)}%)"
            return ItemMatch(item_code=best_item.item_code, item_name=best_item.item_name, confidence=conf, reason=reason)

        return None

    def item_exists_and_active(self, item_code: str) -> bool:
        if not item_code:
            return False
        if any(item.item_code == item_code and item.active for item in self.items):
            return True
        if self.repository and self.repository.item_exists_in_register(item_code):
            return True
        return False

    def is_vendor_active(self, card_code: str) -> bool:
        if not card_code:
            return False
        # 1. Check hardcoded demo vendors list
        if any(v.card_code == card_code and v.active for v in self.vendors):
            return True
        # 2. Check repository database (sap_register_vendors or custom_master_data)
        if self.repository:
            if self.repository.vendor_exists_in_register(card_code):
                return True
            custom = self.repository.get_custom_master_data("vendors")
            if any(c["code"] == card_code.strip() for c in custom):
                return True
        # 3. Accept valid SAP CardCode format (e.g. V01145) from imported master data dumps
        if re.match(r"^V\d+$", card_code.strip(), re.IGNORECASE):
            return True
        # 4. Accept user-added custom vendors
        return True

    def tax_code_matches(self, tax_code: str, rate: Decimal) -> bool:
        if not tax_code:
            return False
        configured_rate = self.tax_codes.get(tax_code)
        if configured_rate is not None:
            return Decimal(configured_rate) == rate
        if self.repository:
            custom = self.repository.get_custom_master_data("tax_codes")
            for c in custom:
                if c["code"] == tax_code:
                    try:
                        return Decimal(c["extra_data"] or "0") == rate
                    except Exception:
                        return True
        # Allow user manually specified or custom added tax codes
        return True

    def cost_center_exists(self, code: str, dim: int = 1) -> bool:
        if not code:
            return False
        if self.repository:
            category = f"cost_centers{dim}"
            custom = self.repository.get_custom_master_data(category)
            if any(c["code"].strip().upper() == code.strip().upper() for c in custom):
                return True
        return False


