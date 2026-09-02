from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.master_data import DemoSapMasterDataGateway
from app.models import DocumentStatus, InvoiceDocument, InvoiceLine, ValidationIssue, ValidationResult
from app.repository import SQLiteRepository

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def determine_sap_tax_code(tax_pct: Decimal, explicit_code: str | None = None) -> str:
    """Determine SAP Business One tax code (e.g. GST18, GST12, GST5, IGST0) from tax percentage."""
    if explicit_code and explicit_code.strip() and explicit_code.strip().upper() not in ("IGST0", "GST0"):
        return explicit_code.strip()
    try:
        pct = Decimal(str(tax_pct or "0")).quantize(Decimal("0.01"))
    except Exception:
        pct = Decimal("0")
    if pct == Decimal("12.00") or pct == Decimal("12"):
        return "GST12"
    elif pct == Decimal("18.00") or pct == Decimal("18"):
        return "GST18"
    elif pct == Decimal("5.00") or pct == Decimal("5"):
        return "GST5"
    elif pct == Decimal("28.00") or pct == Decimal("28"):
        return "GST28"
    elif pct > Decimal("0"):
        return f"GST{int(pct)}"
    return "IGST0"


def is_service_line(line: InvoiceLine) -> bool:
    if line.item_service_type:
        return line.item_service_type.casefold() in ("service", "ddocument_service")
    if line.sap_item_code:
        return False
    if line.gl_account or line.sac_entry is not None:
        return True
    return True


class InvoiceService:
    def __init__(self, repository: SQLiteRepository, master_data: DemoSapMasterDataGateway) -> None:
        self.repository = repository
        self.master_data = master_data

    def map_document(self, document: InvoiceDocument) -> InvoiceDocument:
        supplier_name = document.invoice.invoice_header.supplier_name
        gstin = document.invoice.invoice_header.supplier_gstin

        # 1. Resolve vendor if not already set
        if not document.invoice.supplier.sap_card_code:
            match = self.master_data.find_vendor_advanced(supplier_name, gstin)
            if match:
                document.invoice.supplier.sap_card_code = match.card_code
                document.invoice.supplier.confidence = match.confidence
                document.invoice.supplier.reason = match.reason
        else:
            if self.master_data.is_vendor_active(document.invoice.supplier.sap_card_code):
                document.invoice.supplier.confidence = max(document.invoice.supplier.confidence, 99.8)
                document.invoice.supplier.reason = "User-specified CardCode — validated active"
            else:
                document.invoice.supplier.confidence = max(document.invoice.supplier.confidence, 95.0)
                document.invoice.supplier.reason = "User-specified CardCode (will validate on SAP post)"

        card_code = document.invoice.supplier.sap_card_code
        inv_num = document.invoice.invoice_header.invoice_number or ""

        # 2. Enrich document from vendor records & historical profile
        if card_code:
            document, _, _ = self.enrich_document_from_vendor_history(document, card_code, inv_num)

        # 3. For any item mode lines, resolve item code if not already mapped
        for line in document.invoice.lines:
            if not is_service_line(line) and not line.sap_item_code:
                item_match = self.master_data.find_item_advanced(
                    description=line.description,
                    supplier_item_code=line.supplier_item_code,
                    supplier_name=supplier_name,
                )
                if item_match:
                    line.sap_item_code = item_match.item_code
                    line.mapping_confidence = max(line.mapping_confidence, item_match.confidence)
                    line.mapping_reason = item_match.reason

        document.status = DocumentStatus.MAPPED
        document.updated_at = datetime.now(timezone.utc)
        return self.repository.save(document)

    def validate(self, document: InvoiceDocument) -> InvoiceDocument:
        invoice = document.invoice
        issues: list[ValidationIssue] = []
        card_code = invoice.supplier.sap_card_code

        if not card_code:
            issues.append(ValidationIssue(code="VENDOR_UNMAPPED", message="No SAP CardCode was mapped to the supplier."))
        elif self.repository.duplicate_exists(card_code, invoice.invoice_header.invoice_number):
            issues.append(ValidationIssue(code="DUPLICATE_INVOICE", message=f"Duplicate detected: Invoice '{invoice.invoice_header.invoice_number}' for CardCode '{card_code}' has already been processed or posted."))

        subtotal = money(sum((line.quantity * line.unit_price - line.discount_amount for line in invoice.lines), Decimal("0")))
        tax = money(sum((line.tax_amount for line in invoice.lines), Decimal("0")))
        rounding = money(invoice.totals.rounding_amount or Decimal("0.00"))
        wtax = money(invoice.totals.wtax_amount or Decimal("0.00"))
        total = money(subtotal + tax + rounding - wtax)

        if money(invoice.totals.subtotal) != subtotal:
            issues.append(ValidationIssue(code="SUBTOTAL_MISMATCH", message=f"Extracted subtotal {invoice.totals.subtotal} differs from calculated subtotal {subtotal}."))
        if money(invoice.totals.tax_amount) != tax:
            issues.append(ValidationIssue(code="TAX_TOTAL_MISMATCH", message=f"Extracted tax {invoice.totals.tax_amount} differs from calculated tax {tax}."))
        if money(invoice.totals.grand_total) != total:
            issues.append(ValidationIssue(code="TOTAL_MISMATCH", message=f"Extracted total {invoice.totals.grand_total} differs from calculated total {total}."))

        for line in invoice.lines:
            pre_tax_total = money(line.quantity * line.unit_price - line.discount_amount)
            post_tax_total = money(pre_tax_total + line.tax_amount)
            if money(line.line_total) != pre_tax_total and money(line.line_total) != post_tax_total:
                issues.append(ValidationIssue(code="LINE_TOTAL_MISMATCH", message=f"Expected line total {pre_tax_total} (or {post_tax_total} with tax), received {line.line_total}.", line_number=line.line_number))
            expected_tax = money((line.quantity * line.unit_price - line.discount_amount) * line.tax_percentage / Decimal("100"))
            if money(line.tax_amount) != expected_tax:
                issues.append(ValidationIssue(code="LINE_TAX_MISMATCH", message=f"Expected tax {expected_tax}, received {line.tax_amount}.", line_number=line.line_number))

            # Service mode (dDocument_Service) vs Item mode (dDocument_Item) validation
            if is_service_line(line):
                if line.sac_entry is None and not line.gl_account:
                    issues.append(ValidationIssue(code="SAC_ACCOUNT_UNMAPPED", message="In Service mode (dDocument_Service), an SAC Entry code or G/L Account is required.", line_number=line.line_number))
            else:
                if not line.sap_item_code:
                    issues.append(ValidationIssue(code="ITEM_UNMAPPED", message="In Item mode (dDocument_Item), a valid SAP ItemCode is required.", line_number=line.line_number))
                elif not self.master_data.item_exists_and_active(line.sap_item_code):
                    issues.append(ValidationIssue(code="ITEM_INVALID", message=f"SAP ItemCode {line.sap_item_code} does not exist or is inactive.", line_number=line.line_number))

            if not line.tax_code:
                issues.append(ValidationIssue(code="TAX_CODE_UNMAPPED", message="No SAP tax code is mapped.", line_number=line.line_number))
            elif not self.master_data.tax_code_matches(line.tax_code, line.tax_percentage):
                issues.append(ValidationIssue(code="TAX_CODE_RATE_MISMATCH", message=f"Tax code {line.tax_code} does not match {line.tax_percentage}%.", line_number=line.line_number))

        # Rule 1: GST Transaction Type vs Supplier GSTIN
        header = invoice.invoice_header
        supplier_gstin = (header.supplier_gstin or "").strip()
        expected_gst_type = "gsttrantyp_GSTTaxInvoice" if supplier_gstin else "gsttrantyp_BillOfSupply"
        if header.transaction_type and header.transaction_type != expected_gst_type:
            if supplier_gstin:
                issues.append(ValidationIssue(
                    code="GST_TRANSACTION_TYPE_MISMATCH",
                    message=f"Vendor has GSTIN '{supplier_gstin}'. GST Transaction Type must be 'GST Tax Invoice' (gsttrantyp_GSTTaxInvoice), received '{header.transaction_type}'."
                ))
            else:
                issues.append(ValidationIssue(
                    code="GST_TRANSACTION_TYPE_MISMATCH",
                    message=f"Vendor has no GSTIN. GST Transaction Type must be 'Bill of Supply' (gsttrantyp_BillOfSupply), received '{header.transaction_type}'."
                ))

        # Rule 2: Branch 1 (Tamil Nadu) vs Cost Center 1 (costing_code)
        if header.bpl_id_assigned_to_invoice == 1 or header.branch == "1":
            for line in invoice.lines:
                cc1 = (line.costing_code or "").strip().upper()
                valid_tn_prefix = (cc1.startswith("TN") or "TAMIL" in cc1 or "TN - " in cc1 or "TN-" in cc1 or cc1.startswith("KLR") or cc1.startswith("CENTR"))
                if cc1 and not valid_tn_prefix and not self.master_data.cost_center_exists(cc1, dim=1):
                    issues.append(ValidationIssue(
                        code="INVALID_BRANCH_COST_CENTER",
                        message=f"Branch 1 is Tamil Nadu. Cost Center 1 must be a valid Cost Center (e.g. 'TN - KLR', 'TN - PKD', 'KLR-OFF'), received '{line.costing_code}'.",
                        line_number=line.line_number
                    ))

        if card_code and not self.master_data.is_vendor_active(card_code):
            issues.append(ValidationIssue(code="VENDOR_INACTIVE", message=f"SAP vendor {card_code} is inactive."))

        # ── Dynamic Custom Validation Rules ──
        try:
            active_rules = self.repository.get_validation_rules(active_only=True)
            if active_rules:
                doc_dict = document.invoice.model_dump()
                flat_header = {
                    "supplier_name": doc_dict.get("invoice_header", {}).get("supplier_name"),
                    "invoice_number": doc_dict.get("invoice_header", {}).get("invoice_number"),
                    "grand_total": float(doc_dict.get("totals", {}).get("grand_total") or 0.0),
                    "tax_amount": float(doc_dict.get("totals", {}).get("tax_amount") or 0.0),
                    "subtotal": float(doc_dict.get("totals", {}).get("subtotal") or 0.0),
                    "local_currency": doc_dict.get("invoice_header", {}).get("local_currency"),
                    "transaction_type": doc_dict.get("invoice_header", {}).get("transaction_type"),
                    "bpl_id": doc_dict.get("invoice_header", {}).get("bpl_id_assigned_to_invoice"),
                }
                
                for rule in active_rules:
                    field = rule["target_field"]
                    op = rule["condition"]
                    val = rule["condition_value"]
                    msg = rule["error_message"] or f"Validation failed for rule: {rule['rule_name']}"
                    
                    def evaluate_condition(actual_val, operator, target_val):
                        if operator == "not_empty": return bool(actual_val)
                        if operator == "is_empty": return not bool(actual_val)
                        if actual_val is None: actual_val = ""
                        try:
                            a_num, t_num = float(actual_val), float(target_val)
                            if operator == ">": return a_num > t_num
                            if operator == "<": return a_num < t_num
                            if operator == ">=": return a_num >= t_num
                            if operator == "<=": return a_num <= t_num
                            if operator == "==": return a_num == t_num
                            if operator == "!=": return a_num != t_num
                        except (ValueError, TypeError): pass
                        a_str, t_str = str(actual_val).lower().strip(), str(target_val).lower().strip()
                        if operator == "==": return a_str == t_str
                        if operator == "!=": return a_str != t_str
                        if operator == "contains": return t_str in a_str
                        return True
                        
                    if field in flat_header:
                        if not evaluate_condition(flat_header[field], op, val):
                            issues.append(ValidationIssue(code="CUSTOM_RULE_FAILED", message=msg))
                    else:
                        for line in invoice.lines:
                            line_dict = line.model_dump()
                            if field in line_dict:
                                if not evaluate_condition(line_dict[field], op, val):
                                    issues.append(ValidationIssue(code="CUSTOM_RULE_FAILED", message=msg, line_number=line.line_number))
        except Exception as e:
            import logging
            logging.error(f"Failed to evaluate custom validation rules: {e}")

        confidences = [invoice.supplier.confidence, *(line.mapping_confidence for line in invoice.lines)]
        confidence = min(confidences) if confidences else 0.0
        passed = not issues

        # Section 10 Confidence threshold routing:
        # >= 95% -> Auto process / VALIDATED candidate
        # 80-95% -> User confirmation required / NEEDS_REVIEW
        # < 80% -> Mandatory correction / NEEDS_REVIEW
        status = DocumentStatus.VALIDATED if (passed and confidence >= 95.0) else DocumentStatus.NEEDS_REVIEW

        document.validation = ValidationResult(
            passed=passed,
            issues=issues,
            calculated_subtotal=subtotal,
            calculated_tax=tax,
            calculated_total=total,
            overall_confidence=confidence,
            recommended_status=status,
        )
        document.status = status
        document.updated_at = datetime.now(timezone.utc)
        return self.repository.save(document)

    def learn_from_document(self, document: InvoiceDocument) -> None:
        """Learn vendor profile & line coding modifications upon approval/posting to auto-populate future invoices."""
        card_code = document.invoice.supplier.sap_card_code
        supplier_name = document.invoice.invoice_header.supplier_name or "Unknown Supplier"
        invoice_number = document.invoice.invoice_header.invoice_number or ""
        header = document.invoice.invoice_header
        if not card_code or not document.invoice.lines:
            return

        # 1. Save full invoice snapshot keyed by CardCode + InvoiceNumber for exact-invoice recall
        if invoice_number:
            self.repository.save_invoice_snapshot(
                card_code=card_code,
                invoice_number=invoice_number,
                payload_json=document.model_dump_json(),
            )

        # 2. Learn Vendor Mapping itself (OCR Name -> CardCode)
        if supplier_name and supplier_name != "Unknown Supplier":
            self.repository.record_mapping_correction(
                supplier_name=supplier_name,
                ocr_value=supplier_name,
                ai_value=card_code,
                final_correct_value=card_code,
                mapping_type="VENDOR",
                field_payload=f"Auto-learned from approved document (CardCode={card_code})",
            )
            self.repository.record_mapping_correction(
                supplier_name=card_code,
                ocr_value=card_code,
                ai_value=supplier_name,
                final_correct_value=supplier_name,
                mapping_type="VENDOR_NAME",
                field_payload=f"CardCode={card_code}",
            )

        # 3. Learn vendor-level header defaults
        if header.series is not None:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=str(header.series),
                final_correct_value=str(header.series), mapping_type="VENDOR_DEFAULT_SERIES",
                field_payload="Auto-learned from approved document",
            )
        if header.bpl_id_assigned_to_invoice:
            branch_val = str(header.bpl_id_assigned_to_invoice)
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=branch_val,
                final_correct_value=branch_val, mapping_type="VENDOR_DEFAULT_BRANCH",
                field_payload="Auto-learned from approved document",
            )
        if header.transaction_type:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=header.transaction_type,
                final_correct_value=header.transaction_type, mapping_type="VENDOR_DEFAULT_GST_TRANS_TYPE",
                field_payload="Auto-learned from approved document",
            )
        if header.local_currency:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=header.local_currency,
                final_correct_value=header.local_currency, mapping_type="VENDOR_DEFAULT_CURRENCY",
                field_payload="Auto-learned from approved document",
            )
        if header.payment_group_code:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=str(header.payment_group_code),
                final_correct_value=str(header.payment_group_code), mapping_type="VENDOR_DEFAULT_PAYMENT_TERMS",
                field_payload="Auto-learned from approved document",
            )
        if header.control_account:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=header.control_account,
                final_correct_value=header.control_account, mapping_type="VENDOR_DEFAULT_CONTROL_ACCOUNT",
                field_payload="Auto-learned from approved document",
            )
        if header.place_of_supply:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=header.place_of_supply,
                final_correct_value=header.place_of_supply, mapping_type="VENDOR_DEFAULT_PLACE_OF_SUPPLY",
                field_payload="Auto-learned from approved document",
            )
        if header.wt_code:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=header.wt_code,
                final_correct_value=header.wt_code, mapping_type="VENDOR_DEFAULT_WT_CODE",
                field_payload="Auto-learned from approved document",
            )

        # 4. Learn per-line mappings for EVERY line in document
        for line in document.invoice.lines:
            desc = (line.description or "").strip()
            if not desc:
                continue
            if line.gl_account:
                self.repository.record_mapping_correction(
                    supplier_name=card_code,
                    ocr_value=desc,
                    ai_value=line.gl_account,
                    final_correct_value=line.gl_account,
                    mapping_type="GL_ACCOUNT",
                    field_payload=f"GL={line.gl_account} | SAC={line.sac_entry} | CC1={line.costing_code} | CC2={line.costing_code2} | CC3={line.costing_code3} | Loc={line.location_code} | TaxCode={line.tax_code}",
                )
            if line.sac_entry is not None:
                self.repository.record_mapping_correction(
                    supplier_name=card_code,
                    ocr_value=desc,
                    ai_value=str(line.sac_entry),
                    final_correct_value=str(line.sac_entry),
                    mapping_type="SAC_ENTRY",
                    field_payload=f"SAC={line.sac_entry} | CardCode={card_code}",
                )
            if line.sap_item_code:
                self.repository.record_mapping_correction(
                    supplier_name=card_code,
                    ocr_value=desc,
                    ai_value=line.sap_item_code,
                    final_correct_value=line.sap_item_code,
                    mapping_type="ITEM",
                    field_payload=f"ItemCode={line.sap_item_code} | CardCode={card_code}",
                )

        # 5. Learn default line coding from first line
        first_line = document.invoice.lines[0]
        if first_line.gl_account:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=first_line.gl_account,
                final_correct_value=first_line.gl_account, mapping_type="VENDOR_DEFAULT_GL",
                field_payload="Auto-learned default from approved document",
            )
        if first_line.sac_entry is not None:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=str(first_line.sac_entry),
                final_correct_value=str(first_line.sac_entry), mapping_type="VENDOR_DEFAULT_SAC",
                field_payload="Auto-learned default from approved document",
            )
        if first_line.costing_code:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=first_line.costing_code,
                final_correct_value=first_line.costing_code, mapping_type="VENDOR_DEFAULT_COSTING1",
                field_payload="Auto-learned default from approved document",
            )
        if first_line.costing_code2:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=first_line.costing_code2,
                final_correct_value=first_line.costing_code2, mapping_type="VENDOR_DEFAULT_COSTING2",
                field_payload="Auto-learned default from approved document",
            )
        if first_line.costing_code3 and first_line.costing_code3 != "NONE":
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=first_line.costing_code3,
                final_correct_value=first_line.costing_code3, mapping_type="VENDOR_DEFAULT_COSTING3",
                field_payload="Auto-learned default from approved document",
            )
        if first_line.location_code:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=first_line.location_code,
                final_correct_value=first_line.location_code, mapping_type="VENDOR_DEFAULT_LOCATION",
                field_payload="Auto-learned default from approved document",
            )
        if first_line.wtax_liable:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=first_line.wtax_liable,
                final_correct_value=first_line.wtax_liable, mapping_type="VENDOR_DEFAULT_WTAX_LIABLE",
                field_payload="Auto-learned default from approved document",
            )
        if first_line.wt_code:
            self.repository.record_mapping_correction(
                supplier_name=card_code, ocr_value=card_code, ai_value=first_line.wt_code,
                final_correct_value=first_line.wt_code, mapping_type="VENDOR_DEFAULT_WT_CODE",
                field_payload="Auto-learned default from approved document",
            )

    def get_vendor_defaults(self, card_code: str) -> dict:
        """Retrieve full learned defaults & master data for a vendor to pre-populate all form fields."""
        if not card_code:
            return {}

        card_code_clean = card_code.strip()

        # 1. Fetch vendor master details
        vendor_name = ""
        currency = "INR"
        payment_group = 10
        control_account = None
        payment_method = None
        wtax_liable = "tYES"
        wt_code = "C004"
        place_of_supply = None
        ship_to_address = None
        pay_to_address = None
        ship_to_code = None
        pay_to_code = None
        gstin = None

        sap_master = self.repository.get_sap_vendor_master(card_code_clean)
        if sap_master:
            vm = sap_master[0]
            if vm.get("currency"):
                currency = vm["currency"]
            if vm.get("payment_group"):
                try:
                    payment_group = int(vm["payment_group"])
                except Exception:
                    pass
            if vm.get("gstin"):
                gstin = vm["gstin"]

        custom_data = self.repository.get_custom_master_data("vendors")
        for c in custom_data:
            if c["code"].strip().upper() == card_code_clean.upper():
                vendor_name = c["name"]
                if c.get("extra_data"):
                    try:
                        import json
                        extra = json.loads(c["extra_data"]) if isinstance(c["extra_data"], str) else c["extra_data"]
                        if extra.get("currency"): currency = extra["currency"]
                        if extra.get("gstin"): gstin = extra["gstin"]
                        if extra.get("payment_group"):
                            try: payment_group = int(extra["payment_group"])
                            except Exception: pass
                        if extra.get("place_of_supply"): place_of_supply = extra["place_of_supply"]
                        if extra.get("wtax_liable"): wtax_liable = extra["wtax_liable"]
                        if extra.get("wt_code"): wt_code = extra["wt_code"]
                        if extra.get("control_account"): control_account = extra["control_account"]
                        if extra.get("payment_method"): payment_method = extra["payment_method"]
                    except Exception:
                        pass
                break

        # Check vendor addresses
        addrs = self.repository.get_vendor_addresses(card_code_clean)
        for a in addrs:
            if a.get("address_type") == "ship" and not ship_to_address:
                ship_to_address = a.get("address_text") or a.get("street")
                ship_to_code = a.get("address_code")
            elif a.get("address_type") == "pay" and not pay_to_address:
                pay_to_address = a.get("address_text") or a.get("street")
                pay_to_code = a.get("address_code")
            if not place_of_supply and a.get("state"):
                place_of_supply = a.get("state")

        # 2. Check learned mapping history defaults
        default_series = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_SERIES")
        default_branch = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_BRANCH")
        default_gst_trans = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_GST_TRANS_TYPE")
        default_gl = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_GL")
        default_sac = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_SAC")
        default_c1 = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_COSTING1")
        default_c2 = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_COSTING2")
        default_c3 = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_COSTING3")
        default_loc = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_LOCATION")
        default_wt = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_WT_CODE")
        default_wtax_l = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_WTAX_LIABLE")
        default_curr = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_CURRENCY")
        default_pay_terms = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_PAYMENT_TERMS")
        default_ctrl_acc = self.repository.get_historical_mapping(ocr_value=card_code_clean, mapping_type="VENDOR_DEFAULT_CONTROL_ACCOUNT")

        # 3. Check most recent posted document for this vendor to fill any remaining gaps
        prev_doc = self.repository.get_latest_posted_by_vendor(card_code_clean)
        if prev_doc:
            ph = prev_doc.invoice.invoice_header
            if ph.series is not None and not default_series: default_series = str(ph.series)
            if ph.bpl_id_assigned_to_invoice and not default_branch: default_branch = str(ph.bpl_id_assigned_to_invoice)
            if ph.branch and not default_branch: default_branch = str(ph.branch)
            if ph.transaction_type and not default_gst_trans: default_gst_trans = ph.transaction_type
            if ph.local_currency and ph.local_currency != "INR" and not default_curr: default_curr = ph.local_currency
            if ph.payment_group_code and ph.payment_group_code != 10 and not default_pay_terms: default_pay_terms = str(ph.payment_group_code)
            if ph.control_account and not default_ctrl_acc: default_ctrl_acc = ph.control_account
            if ph.place_of_supply and not place_of_supply: place_of_supply = ph.place_of_supply
            if ph.ship_to_address and not ship_to_address: ship_to_address = ph.ship_to_address
            if ph.pay_to_address and not pay_to_address: pay_to_address = ph.pay_to_address
            if ph.wt_code and not default_wt: default_wt = ph.wt_code

            if prev_doc.invoice.lines:
                pl0 = prev_doc.invoice.lines[0]
                if pl0.gl_account and not default_gl: default_gl = pl0.gl_account
                if pl0.sac_entry is not None and not default_sac: default_sac = str(pl0.sac_entry)
                if pl0.costing_code and not default_c1: default_c1 = pl0.costing_code
                if pl0.costing_code2 and not default_c2: default_c2 = pl0.costing_code2
                if pl0.costing_code3 and pl0.costing_code3 != "NONE" and not default_c3: default_c3 = pl0.costing_code3
                if pl0.location_code and not default_loc: default_loc = pl0.location_code
                if pl0.wt_code and not default_wt: default_wt = pl0.wt_code
                if pl0.wtax_liable and not default_wtax_l: default_wtax_l = pl0.wtax_liable

        series_val = int(default_series) if default_series and default_series.isdigit() else 521
        branch_val = int(default_branch) if default_branch and default_branch.isdigit() else 1
        pay_group_val = int(default_pay_terms) if default_pay_terms and default_pay_terms.isdigit() else payment_group

        return {
            "card_code": card_code_clean,
            "supplier_name": vendor_name,
            "supplier_gstin": gstin,
            "series": series_val,
            "branch": str(branch_val),
            "bpl_id_assigned_to_invoice": branch_val,
            "transaction_type": default_gst_trans or "gsttrantyp_BillOfSupply",
            "local_currency": default_curr or currency or "INR",
            "payment_group_code": pay_group_val,
            "control_account": default_ctrl_acc or control_account,
            "payment_method": payment_method,
            "place_of_supply": place_of_supply,
            "ship_to_address": ship_to_address,
            "pay_to_address": pay_to_address,
            "ship_to_code": ship_to_code,
            "pay_to_code": pay_to_code,
            "wt_code": default_wt or wt_code,
            "wtax_liable": default_wtax_l or wtax_liable,
            "default_gl": default_gl,
            "default_sac": default_sac,
            "default_costing1": default_c1,
            "default_costing2": default_c2,
            "default_costing3": default_c3,
            "default_location": default_loc,
        }

    def enrich_document_from_vendor_history(
        self,
        document: InvoiceDocument,
        card_code: str,
        invoice_number: str | None = None,
    ) -> tuple[InvoiceDocument, bool, str | None]:
        """Overlay vendor-specific profile & historical coding defaults onto document.

        Strictly preserves all extracted document OCR fields:
        - invoice_number, invoice_date, document_date, posting_date, due_date
        - unit_price, quantity, line_total, tax_amount, tax_percentage
        - subtotal, grand_total, supplier_gstin, supplier_name

        Auto-fills all remaining fields from vendor records & historical postings:
        - series, branch, bpl_id_assigned_to_invoice, transaction_type, currency, payment_terms, control_account
        - gl_account, sac_entry, tax_code, costing_code, costing_code2, costing_code3, location_code, wtax_liable, wt_code
        """
        if not card_code:
            return document, False, None

        card_code_clean = card_code.strip()
        inv_header = document.invoice.invoice_header
        inv_num = (invoice_number or inv_header.invoice_number or "").strip()

        # 1. Fetch vendor master & learned defaults
        defaults = self.get_vendor_defaults(card_code_clean)
        
        # Auto-fill missing GSTIN from master data
        if not inv_header.supplier_gstin or inv_header.supplier_gstin.strip() == "":
            for v in self.master_data.vendors:
                if v.card_code == card_code_clean and v.gstin:
                    inv_header.supplier_gstin = v.gstin
                    break

        # 2. Check for exact snapshot or previous posted document
        previous_doc_id: str | None = None
        prev_doc: InvoiceDocument | None = None
        found = False

        if inv_num:
            snapshot_json = self.repository.get_invoice_snapshot(card_code_clean, inv_num)
            if snapshot_json:
                try:
                    prev_doc = InvoiceDocument.model_validate_json(snapshot_json)
                    found = True
                    previous_doc_id = str(prev_doc.document_id)
                except Exception:
                    prev_doc = None

        if not prev_doc and inv_num:
            prev_doc = self.repository.get_posted_document_by_invoice(card_code_clean, inv_num)
            if prev_doc:
                found = True
                previous_doc_id = str(prev_doc.document_id)

        if not prev_doc:
            prev_doc = self.repository.get_latest_posted_by_vendor(card_code_clean)
            if prev_doc:
                found = True
                previous_doc_id = str(prev_doc.document_id)

        # 3. Enrich Header Fields
        document.invoice.supplier.sap_card_code = card_code_clean
        document.invoice.supplier.confidence = max(document.invoice.supplier.confidence, 98.0)
        document.invoice.supplier.reason = f"Matched to vendor profile ({card_code_clean})"

        if inv_header.series is None:
            inv_header.series = defaults.get("series", 521)
        if not inv_header.bpl_id_assigned_to_invoice or inv_header.bpl_id_assigned_to_invoice == 1:
            inv_header.bpl_id_assigned_to_invoice = defaults.get("bpl_id_assigned_to_invoice", 1)
        if not inv_header.branch or inv_header.branch == "1":
            inv_header.branch = str(defaults.get("branch", "1"))

        # GST Transaction Type alignment:
        if inv_header.supplier_gstin and inv_header.supplier_gstin.strip():
            inv_header.transaction_type = "gsttrantyp_GSTTaxInvoice"
        elif not inv_header.transaction_type:
            inv_header.transaction_type = defaults.get("transaction_type", "gsttrantyp_BillOfSupply")

        if not inv_header.local_currency or inv_header.local_currency == "INR":
            inv_header.local_currency = defaults.get("local_currency", "INR")
        if not inv_header.payment_group_code or inv_header.payment_group_code == 10:
            inv_header.payment_group_code = defaults.get("payment_group_code", 10)
        if not inv_header.control_account and defaults.get("control_account"):
            inv_header.control_account = defaults.get("control_account")
        if not inv_header.payment_method and defaults.get("payment_method"):
            inv_header.payment_method = defaults.get("payment_method")
        if not inv_header.place_of_supply and defaults.get("place_of_supply"):
            inv_header.place_of_supply = defaults.get("place_of_supply")
        if not inv_header.ship_to_address and defaults.get("ship_to_address"):
            inv_header.ship_to_address = defaults.get("ship_to_address")
        if not inv_header.pay_to_address and defaults.get("pay_to_address"):
            inv_header.pay_to_address = defaults.get("pay_to_address")
        if not inv_header.ship_to_code and defaults.get("ship_to_code"):
            inv_header.ship_to_code = defaults.get("ship_to_code")
        if not inv_header.pay_to_code and defaults.get("pay_to_code"):
            inv_header.pay_to_code = defaults.get("pay_to_code")
        if not inv_header.wt_code:
            inv_header.wt_code = defaults.get("wt_code", "C004")
        if not inv_header.journal_memo:
            inv_header.journal_memo = f"A/P Invoices - {card_code_clean}"

        # 4. Enrich Line-Level Coding Fields
        prev_lines = prev_doc.invoice.lines if prev_doc else []

        for curr_line in document.invoice.lines:
            # Determine appropriate SAP Tax Code for the OCR-extracted tax percentage
            curr_line.tax_code = determine_sap_tax_code(curr_line.tax_percentage, curr_line.tax_code)

            # Match against prior posting line if available
            prev_line = next((pl for pl in prev_lines if pl.line_number == curr_line.line_number), None)
            if not prev_line and prev_lines:
                curr_desc = (curr_line.description or "").strip().lower()
                best_score = 0
                for pl in prev_lines:
                    pl_desc = (pl.description or "").strip().lower()
                    overlap = len(set(curr_desc.split()) & set(pl_desc.split()))
                    if overlap > best_score:
                        best_score = overlap
                        prev_line = pl
                if not prev_line:
                    prev_line = prev_lines[0]

            # Lookup description memory in mapping_history
            desc_val = (curr_line.description or "").strip()
            hist_gl = self.repository.get_historical_mapping(desc_val, "GL_ACCOUNT", card_code_clean)
            hist_sac = self.repository.get_historical_mapping(desc_val, "SAC_ENTRY", card_code_clean)
            hist_item = self.repository.get_historical_mapping(desc_val, "ITEM", card_code_clean)

            # Auto-fill G/L Account
            if not curr_line.gl_account:
                curr_line.gl_account = hist_gl or (prev_line.gl_account if prev_line else None) or defaults.get("default_gl")

            # Auto-fill SAC Entry
            if curr_line.sac_entry is None:
                sac_cand = hist_sac or (prev_line.sac_entry if prev_line else None) or defaults.get("default_sac")
                try:
                    curr_line.sac_entry = int(sac_cand) if sac_cand is not None else None
                except Exception:
                    curr_line.sac_entry = None

            # Auto-fill Costing Codes (Dimensions 1, 2, 3)
            if not curr_line.costing_code:
                curr_line.costing_code = (prev_line.costing_code if prev_line else None) or defaults.get("default_costing1")
            if not curr_line.costing_code2:
                curr_line.costing_code2 = (prev_line.costing_code2 if prev_line else None) or defaults.get("default_costing2")
            if not curr_line.costing_code3 or curr_line.costing_code3 == "NONE":
                curr_line.costing_code3 = (prev_line.costing_code3 if prev_line else None) or defaults.get("default_costing3")
            if not curr_line.costing_code3:
                curr_line.costing_code3 = "NONE"

            # Auto-fill Location Code
            if not curr_line.location_code:
                curr_line.location_code = (prev_line.location_code if prev_line else None) or defaults.get("default_location")

            # Auto-fill WTax Liable & WT Code
            if not curr_line.wtax_liable:
                curr_line.wtax_liable = (prev_line.wtax_liable if prev_line else None) or defaults.get("wtax_liable", "tYES")
            if not curr_line.wt_code:
                curr_line.wt_code = (prev_line.wt_code if prev_line else None) or defaults.get("wt_code", "C004")

            # Item / Service Mode
            if not curr_line.item_service_type:
                curr_line.item_service_type = (prev_line.item_service_type if prev_line else None) or ("Service" if (curr_line.gl_account or curr_line.sac_entry is not None) else "Item")

            if not curr_line.sap_item_code and hist_item:
                curr_line.sap_item_code = hist_item
            elif not curr_line.sap_item_code and prev_line and prev_line.sap_item_code:
                curr_line.sap_item_code = prev_line.sap_item_code

            curr_line.mapping_confidence = max(curr_line.mapping_confidence, 98.0)
            curr_line.mapping_reason = f"Auto-filled from vendor record ({card_code_clean})"

        return document, True, previous_doc_id

    def apply_previous_post_data(
        self,
        document: InvoiceDocument,
        card_code: str,
        invoice_number: str,
    ) -> tuple[InvoiceDocument, bool, str | None]:
        """Overlay mapping/coding fields from vendor records & historical postings onto document."""
        return self.enrich_document_from_vendor_history(document, card_code, invoice_number)

    def sap_draft_payload(self, document: InvoiceDocument) -> dict:
        if not document.validation or not document.validation.passed:
            raise ValueError("Document must pass business rule validation before SAP draft JSON can be generated.")
        if document.status not in (DocumentStatus.APPROVED, DocumentStatus.SAP_DRAFT_READY):
            raise ValueError("Document must be approved before SAP draft JSON can be generated.")
        header = document.invoice.invoice_header
        rounding_val = float(document.invoice.totals.rounding_amount) if document.invoice.totals.rounding_amount else 0.0
        doc_type = "dDocument_Service"
        if any(line.item_service_type == "Item" for line in document.invoice.lines):
            doc_type = "dDocument_Items"

        payload = {
            "Series": header.series or 521,
            "DocObjectCode": "oPurchaseInvoices",
            "DocType": doc_type,
            "CardCode": document.invoice.supplier.sap_card_code,
            "DocDate": (header.document_date or header.invoice_date).isoformat(),
            "DocDueDate": (header.due_date or header.document_date or header.invoice_date).isoformat(),
            "TaxDate": (header.posting_date or header.document_date or header.invoice_date).isoformat(),
            "BPL_IDAssignedToInvoice": header.bpl_id_assigned_to_invoice,
            "GSTTransactionType": header.transaction_type or "gsttrantyp_BillOfSupply",
            "NumAtCard": header.invoice_number,
            "DocCurrency": header.local_currency,
            "PaymentGroupCode": header.payment_group_code,
            "JournalMemo": header.journal_memo or f"A/P Invoices - {document.invoice.supplier.sap_card_code or ''}",
            "ControlAccount": header.control_account,
            "PaymentMethod": header.payment_method,
            "CentralBankIndicator": header.central_bank_indicator,
            "Address2": header.ship_to_address,
            "Address": header.pay_to_address,
            "PayToCode": header.pay_to_code,
            "ShipToCode": header.ship_to_code,
            "TransportationCode": header.transportation_code,
            "PlaceOfSupply": header.place_of_supply,
            "Comments": header.comments or f"Created through AI OCR Automation; DocumentID={document.document_id}",
            "Rounding": "tYES" if abs(rounding_val) > 0.0001 else "tNO",
            "RoundingDiffAmount": rounding_val,
            "DocumentLines": [],
        }

        if header.wt_code:
            payload["WithholdingTaxDataCollection"] = [{"WTCode": header.wt_code}]
            
        if document.source_filename:
            file_path = self.repository.get_latest_upload_path_by_filename(document.source_filename)
            if file_path:
                payload["AttachmentFile"] = {
                    "filename": document.source_filename,
                    "path": file_path
                }

        # Clean None values from top-level payload
        payload = {k: v for k, v in payload.items() if v is not None}
        
        # Remove empty strings for Logistics fields (SAP B1 rejects empty strings for these)
        for field in ["Address2", "Address", "PlaceOfSupply", "PayToCode", "ShipToCode"]:
            if field in payload and str(payload[field]).strip() == "":
                del payload[field]

        for idx, line in enumerate(document.invoice.lines):
            unit_p = float(line.unit_price)
            qty = float(line.quantity)
            line_tot = float(line.line_total) if line.line_total else (unit_p * qty)
            tax_amt = float(line.tax_amount) if line.tax_amount else 0.0
            gross_tot = float(line.gross_total) if line.gross_total else (line_tot + tax_amt)

            wtax_liable_flag = "tYES" if str(getattr(line, "wtax_liable", "tYES")).upper() in ("TYES", "YES", "BOYES") else "tNO"
            wt_code_val = getattr(line, "wt_code", "C004") or "C004"

            entry = {
                "LineNum": idx,
                "SACEntry": line.sac_entry,
                "Price": unit_p,
                "UnitPrice": unit_p,
                "AccountCode": line.gl_account,
                "TaxCode": line.tax_code,
                "WTLiable": wtax_liable_flag,
                "WTCode": wt_code_val if wtax_liable_flag == "tYES" else None,
                "LocationCode": line.location_code,
                "CostingCode": line.costing_code,
                "CostingCode2": line.costing_code2,
                "CostingCode3": line.costing_code3,
                "Quantity": qty,
                "ItemDescription": line.description,
                "ItemCode": line.sap_item_code,
                "GrossPrice": float(line.gross_price) if line.gross_price else (gross_tot / max(qty, 1.0)),
                "GrossTotal": gross_tot,
                "TaxTotal": tax_amt,
                "LineTotal": line_tot,
            }
            entry = {k: v for k, v in entry.items() if v not in (None, "")}
            payload["DocumentLines"].append(entry)

        if document.source_filename:
            file_path = self.repository.database_path.parent / "uploads" / document.source_filename
            if file_path.exists():
                payload["AttachmentFile"] = {
                    "filename": document.source_filename,
                    "path": str(file_path),
                }

        return payload


    def compare_ocr_vs_sap(self, document: InvoiceDocument) -> dict:
        """Compare raw OCR extraction against final posted SAP payload to record AI feedback deltas."""
        header = document.invoice.invoice_header
        supplier = document.invoice.supplier
        lines = document.invoice.lines

        ocr_summary = {
            "supplier_name": header.supplier_name,
            "invoice_number": header.invoice_number,
            "invoice_date": header.invoice_date.isoformat(),
            "card_code": supplier.sap_card_code,
            "lines": [{"description": l.description, "sap_item_code": l.sap_item_code, "gl_account": l.gl_account, "total": float(l.line_total)} for l in lines],
        }

        posted_payload = self.sap_draft_payload(document) if (document.validation and document.validation.passed and document.status in (DocumentStatus.APPROVED, DocumentStatus.SAP_DRAFT_READY)) else {}

        # Compute field matching accuracy
        total_fields = 1 + len(lines)
        matched_fields = 0

        deltas = []

        if supplier.sap_card_code:
            matched_fields += 1
            vendor_payload = f"CardCode={supplier.sap_card_code} | GSTIN={header.supplier_gstin or 'N/A'}"
            self.repository.record_mapping_correction(
                supplier_name=header.supplier_name,
                ocr_value=header.supplier_name,
                ai_value=supplier.sap_card_code,
                final_correct_value=supplier.sap_card_code,
                mapping_type="VENDOR",
                field_payload=vendor_payload,
            )
        else:
            deltas.append({"field": "CardCode", "ocr": header.supplier_name, "posted": None, "corrected": True})

        for line in lines:
            lookup_key = line.supplier_item_code or line.description
            line_payload_str = (
                f"LineNum={line.line_number} | GL={line.gl_account} | SAC={line.sac_entry} | "
                f"Price={line.unit_price} | Tax={line.tax_code}({line.tax_percentage}%) | "
                f"CC1={line.costing_code} | CC2={line.costing_code2} | CC3={line.costing_code3}"
            )
            saved_any = False
            
            if line.sap_item_code:
                self.repository.record_mapping_correction(
                    supplier_name=header.supplier_name,
                    ocr_value=lookup_key,
                    ai_value=str(line.sap_item_code),
                    final_correct_value=str(line.sap_item_code),
                    mapping_type="ITEM",
                    field_payload=line_payload_str,
                )
                saved_any = True
                
            if line.gl_account:
                self.repository.record_mapping_correction(
                    supplier_name=header.supplier_name,
                    ocr_value=lookup_key,
                    ai_value=str(line.gl_account),
                    final_correct_value=str(line.gl_account),
                    mapping_type="GL_ACCOUNT",
                    field_payload=line_payload_str,
                )
                saved_any = True
                
            if line.sac_entry:
                self.repository.record_mapping_correction(
                    supplier_name=header.supplier_name,
                    ocr_value=lookup_key,
                    ai_value=str(line.sac_entry),
                    final_correct_value=str(line.sac_entry),
                    mapping_type="SAC_ENTRY",
                    field_payload=line_payload_str,
                )
                saved_any = True

            if saved_any:
                matched_fields += 1
            else:
                deltas.append({"field": f"Line {line.line_number} ItemCode/GL", "ocr": line.description, "posted": None, "corrected": True})

        accuracy_score = round((matched_fields / max(total_fields, 1)) * 100, 1)

        return {
            "document_id": str(document.document_id),
            "supplier_name": header.supplier_name,
            "card_code": supplier.sap_card_code,
            "invoice_number": header.invoice_number,
            "accuracy_score": accuracy_score,
            "total_fields": total_fields,
            "matched_fields": matched_fields,
            "ocr_summary": ocr_summary,
            "posted_payload": posted_payload,
            "deltas": deltas,
        }

    def compare_ocr_vs_sap_response(self, document: InvoiceDocument, sap_response: dict) -> dict:
        """Compare posting request fields against actual returned SAP response body for AI model training."""
        header = document.invoice.invoice_header
        supplier = document.invoice.supplier
        lines = document.invoice.lines

        doc_entry = sap_response.get("DocEntry")
        doc_num = sap_response.get("DocNum")
        sap_doc_total = sap_response.get("DocTotal")
        sap_vat_sum = sap_response.get("VatSum")
        sap_card_code = sap_response.get("CardCode") or supplier.sap_card_code

        # 1. Record SAP DocNum & DocEntry Registration
        self.repository.record_mapping_correction(
            supplier_name=header.supplier_name,
            ocr_value="DocNum & DocEntry",
            ai_value=f"VendorRef: {header.invoice_number}",
            final_correct_value=f"DocNum: #{doc_num} (DocEntry: {doc_entry})",
            mapping_type="SAP_DOC_REGISTER",
            field_payload=f"CAPTURED ✅ (SAP Document Registered Successfully)",
        )

        # 2. Record Header Total Comparison (Request vs SAP Response)
        req_total = float(document.invoice.totals.grand_total)
        resp_total = float(sap_doc_total) if sap_doc_total is not None else req_total
        total_diff = abs(req_total - resp_total)
        total_status = f"MATCH ✅ (Diff: {total_diff:.2f})" if total_diff < 0.05 else f"DELTA ⚠️ (Diff: {total_diff:.2f})"

        self.repository.record_mapping_correction(
            supplier_name=header.supplier_name,
            ocr_value="DocTotal (Grand Total)",
            ai_value=f"ReqTotal: INR {req_total:.2f}",
            final_correct_value=f"SapTotal: INR {resp_total:.2f}",
            mapping_type="SAP_TOTAL_COMPARE",
            field_payload=f"{total_status} | Sent: {req_total} | SAP: {resp_total}",
        )

        # 3. Record Tax Amount Comparison (Request vs SAP Response)
        req_tax = float(document.invoice.totals.tax_amount)
        resp_tax = float(sap_vat_sum) if sap_vat_sum is not None else req_tax
        tax_diff = abs(req_tax - resp_tax)
        tax_status = f"MATCH ✅ (Diff: {tax_diff:.2f})" if tax_diff < 0.05 else f"DELTA ⚠️ (Diff: {tax_diff:.2f})"

        self.repository.record_mapping_correction(
            supplier_name=header.supplier_name,
            ocr_value="VatSum (Tax Amount)",
            ai_value=f"ReqTax: INR {req_tax:.2f}",
            final_correct_value=f"SapTax: INR {resp_tax:.2f}",
            mapping_type="SAP_TAX_COMPARE",
            field_payload=f"{tax_status} | Sent: {req_tax} | SAP: {resp_tax}",
        )

        # 4. Record Line-Level Response Comparisons
        sap_lines = sap_response.get("DocumentLines", [])
        for idx, line in enumerate(lines):
            sap_line = sap_lines[idx] if idx < len(sap_lines) else {}
            req_price = float(line.unit_price)
            resp_price = float(sap_line.get("Price", req_price))
            price_diff = abs(req_price - resp_price)
            price_status = "MATCH ✅" if price_diff < 0.05 else f"DELTA ⚠️ (Diff: {price_diff:.2f})"

            self.repository.record_mapping_correction(
                supplier_name=header.supplier_name,
                ocr_value=f"Line {idx+1} ({line.description}) - Price",
                ai_value=f"ReqPrice: {req_price:.2f}",
                final_correct_value=f"SapPrice: {resp_price:.2f}",
                mapping_type="SAP_LINE_PRICE",
                field_payload=f"{price_status} | GL={line.gl_account} | TaxCode={line.tax_code}",
            )

        return {
            "status": "ok",
            "doc_num": doc_num,
            "doc_entry": doc_entry,
            "sap_doc_total": sap_doc_total,
            "sap_vat_sum": sap_vat_sum,
        }

    def train_ai_model_from_records(self, records: list[dict]) -> dict:
        """Trains the AI Mapping Engine by seeding mapping_history from dump records."""
        vendor_count = 0
        item_count = 0
        gl_count = 0
        total_records = len(records)

        for rec in records:
            supplier_name = str(rec.get("supplier_name") or rec.get("CardName") or "").strip()
            card_code = str(rec.get("card_code") or rec.get("CardCode") or "").strip()

            if supplier_name and card_code:
                self.repository.record_mapping_correction(
                    supplier_name=supplier_name,
                    ocr_value=supplier_name,
                    ai_value=card_code,
                    final_correct_value=card_code,
                    mapping_type="VENDOR",
                    field_payload=f"CardCode={card_code} | Source=BulkTraining",
                )
                vendor_count += 1

            lines = rec.get("lines") or []
            for line in lines:
                desc = str(line.get("description") or line.get("ItemDescription") or line.get("ItemName") or "").strip()
                item_code = str(line.get("item_code") or line.get("ItemCode") or "").strip()
                gl_account = str(line.get("gl_account") or line.get("AccountCode") or "").strip()
                tax_code = str(line.get("tax_code") or line.get("TaxCode") or "").strip()

                if desc and item_code:
                    self.repository.record_mapping_correction(
                        supplier_name=supplier_name,
                        ocr_value=desc,
                        ai_value=item_code,
                        final_correct_value=item_code,
                        mapping_type="ITEM",
                        field_payload=f"ItemCode={item_code} | GL={gl_account} | Source=BulkTraining",
                    )
                    item_count += 1

                if desc and gl_account:
                    self.repository.record_mapping_correction(
                        supplier_name=supplier_name,
                        ocr_value=desc,
                        ai_value=gl_account,
                        final_correct_value=gl_account,
                        mapping_type="GL_ACCOUNT",
                        field_payload=f"GL={gl_account} | TaxCode={tax_code} | Source=BulkTraining",
                    )
                    gl_count += 1
            
            # Learn vendor-level defaults from the first line of the historical document
            if lines:
                first_line = lines[0]
                gl_acc = str(first_line.get("gl_account") or first_line.get("AccountCode") or "").strip()
                cc1 = str(first_line.get("costing_code") or first_line.get("CostingCode") or "").strip()
                cc2 = str(first_line.get("costing_code2") or first_line.get("CostingCode2") or "").strip()
                cc3 = str(first_line.get("costing_code3") or first_line.get("CostingCode3") or "").strip()
                loc = str(first_line.get("location_code") or first_line.get("LocationCode") or "").strip()

                if gl_acc:
                    self.repository.record_mapping_correction(
                        supplier_name=supplier_name, ocr_value=card_code, ai_value=gl_acc, final_correct_value=gl_acc, mapping_type="VENDOR_DEFAULT_GL", field_payload="Bulk Training"
                    )
                if cc1:
                    self.repository.record_mapping_correction(
                        supplier_name=supplier_name, ocr_value=card_code, ai_value=cc1, final_correct_value=cc1, mapping_type="VENDOR_DEFAULT_COSTING1", field_payload="Bulk Training"
                    )
                if cc2:
                    self.repository.record_mapping_correction(
                        supplier_name=supplier_name, ocr_value=card_code, ai_value=cc2, final_correct_value=cc2, mapping_type="VENDOR_DEFAULT_COSTING2", field_payload="Bulk Training"
                    )
                if cc3 and cc3 != "NONE":
                    self.repository.record_mapping_correction(
                        supplier_name=supplier_name, ocr_value=card_code, ai_value=cc3, final_correct_value=cc3, mapping_type="VENDOR_DEFAULT_COSTING3", field_payload="Bulk Training"
                    )
                if loc:
                    self.repository.record_mapping_correction(
                        supplier_name=supplier_name, ocr_value=card_code, ai_value=loc, final_correct_value=loc, mapping_type="VENDOR_DEFAULT_LOCATION", field_payload="Bulk Training"
                    )
            
            # Learn vendor-level header defaults
            series = rec.get("series")
            bpl_id = rec.get("bpl_id_assigned_to_invoice")
            if series is not None:
                self.repository.record_mapping_correction(
                    supplier_name=supplier_name, ocr_value=card_code, ai_value=str(series), final_correct_value=str(series), mapping_type="VENDOR_DEFAULT_SERIES", field_payload="Bulk Training"
                )
            if bpl_id is not None:
                self.repository.record_mapping_correction(
                    supplier_name=supplier_name, ocr_value=card_code, ai_value=str(bpl_id), final_correct_value=str(bpl_id), mapping_type="VENDOR_DEFAULT_BRANCH", field_payload="Bulk Training"
                )

        stats = self.repository.get_ai_training_stats()

        return {
            "status": "success",
            "message": f"Successfully trained AI model on {total_records} historical invoice records!",
            "processed_records": total_records,
            "vendor_rules_learned": vendor_count,
            "item_rules_learned": item_count,
            "gl_rules_learned": gl_count,
            "current_ai_stats": stats,
        }

    def train_ai_from_file_dump(self, file_content: bytes, filename: str) -> dict:
        import io
        import json
        import csv
        records = []

        fn_lower = filename.lower()
        if fn_lower.endswith(".json"):
            text = file_content.decode("utf-8", errors="ignore")
            data = json.loads(text)
            records = data.get("records", data) if isinstance(data, dict) else data
            if not isinstance(records, list):
                records = [records]
        elif fn_lower.endswith(".csv"):
            text = file_content.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            grouped = {}
            for idx, row in enumerate(reader):
                inv_num = str(row.get("Invoice Number") or row.get("Inv No") or row.get("DocNum") or row.get("CardCode") or idx).strip()
                sup_name = str(row.get("Supplier Name") or row.get("Vendor Name") or row.get("CardName") or "").strip()
                card_code = str(row.get("CardCode") or row.get("Vendor Code") or "").strip()

                desc = str(row.get("Description") or row.get("Item Description") or row.get("ItemName") or "").strip()
                item_code = str(row.get("ItemCode") or row.get("Item Code") or "").strip()
                gl_account = str(row.get("AccountCode") or row.get("GL Account") or row.get("G/L Account") or "").strip()
                tax_code = str(row.get("TaxCode") or row.get("Tax Code") or "").strip()

                if inv_num not in grouped:
                    grouped[inv_num] = {
                        "supplier_name": sup_name,
                        "card_code": card_code,
                        "lines": []
                    }
                if desc or item_code or gl_account:
                    grouped[inv_num]["lines"].append({
                        "description": desc,
                        "item_code": item_code,
                        "gl_account": gl_account,
                        "tax_code": tax_code,
                    })

            records = list(grouped.values())
        elif fn_lower.endswith((".xlsx", ".xls")):
            try:
                import pandas as pd
                df = pd.read_excel(io.BytesIO(file_content))
                grouped = {}
                for idx, row in df.iterrows():
                    inv_num = str(row.get("Invoice Number") or row.get("Inv No") or row.get("DocNum") or row.get("CardCode") or idx).strip()
                    sup_name = str(row.get("Supplier Name") or row.get("Vendor Name") or row.get("CardName") or "").strip()
                    card_code = str(row.get("CardCode") or row.get("Vendor Code") or "").strip()

                    desc = str(row.get("Description") or row.get("Item Description") or row.get("ItemName") or "").strip()
                    item_code = str(row.get("ItemCode") or row.get("Item Code") or "").strip()
                    gl_account = str(row.get("AccountCode") or row.get("GL Account") or row.get("G/L Account") or "").strip()
                    tax_code = str(row.get("TaxCode") or row.get("Tax Code") or "").strip()

                    if inv_num not in grouped:
                        grouped[inv_num] = {
                            "supplier_name": sup_name,
                            "card_code": card_code,
                            "lines": []
                        }
                    if desc or item_code or gl_account:
                        grouped[inv_num]["lines"].append({
                            "description": desc,
                            "item_code": item_code,
                            "gl_account": gl_account,
                            "tax_code": tax_code,
                        })

                records = list(grouped.values())
            except Exception as exc:
                log.warning("Failed to parse Excel file: %s", exc)

        return self.train_ai_model_from_records(records)

    def train_ai_from_sap_history(self, top: int = 100) -> dict:
        from .erp.factory import get_erp_client
        raw_invoices = get_erp_client().fetch_historical_ap_invoices(top=top)
        records = []
        for inv in raw_invoices:
            sup_name = inv.get("CardName") or ""
            card_code = inv.get("CardCode") or ""
            series = inv.get("Series")
            bpl_id = inv.get("BPL_IDAssignedToInvoice")
            lines_raw = inv.get("DocumentLines") or []
            lines = []
            for l in lines_raw:
                lines.append({
                    "description": l.get("ItemDescription") or l.get("Dscription") or "",
                    "item_code": l.get("ItemCode") or "",
                    "gl_account": l.get("AccountCode") or "",
                    "tax_code": l.get("TaxCode") or "",
                    "costing_code": l.get("CostingCode") or "",
                    "costing_code2": l.get("CostingCode2") or "",
                    "costing_code3": l.get("CostingCode3") or "",
                    "location_code": l.get("LocationCode") or "",
                })
            records.append({
                "supplier_name": sup_name,
                "card_code": card_code,
                "series": series,
                "bpl_id_assigned_to_invoice": bpl_id,
                "lines": lines,
            })
        return self.train_ai_model_from_records(records)
