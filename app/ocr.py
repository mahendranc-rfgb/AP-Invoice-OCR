"""Local OCR extraction and upload persistence adapters."""

from __future__ import annotations

import json
import os
import re
import sqlite3

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field

from dotenv import load_dotenv

load_dotenv()

from app.models import InvoiceHeader, InvoiceLine, InvoiceTotals, StandardInvoice, SupplierMapping
from app.settings import settings


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SUPPORTED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/tiff"}

_DEFAULT_AI_SYSTEM_PROMPT = """You are an expert SAP Business One A/P Invoice Extraction & Mapping AI model.
Your task is to parse raw OCR invoice text into structured SAP A/P Invoice JSON.
- Line Items: Extract EVERY single line item from the document. Do not skip any lines, even if they look similar or have identical descriptions. Ensure quantities are extracted accurately (e.g. 3.030).
- GST Tax Rules: Extract CGST, SGST, and IGST rates and amounts separately and accurately. Combine equal CGST + SGST (e.g., 6% + 6% -> 12% total, SAP Tax Code 'GST12'). If a tax is present, do not leave it at 0.0.
- Pricing Calculation: Line Item Price & Subtotal must be pre-tax (e.g. 9090.00). Tax amount must be calculated separately (e.g. 1090.80), resulting in Grand Total (10180.80 / 10181.00).
- Master Data Lookup: Match Vendor GSTIN against SAP Master record and populate CardCode (e.g., V00101).
- Mode Scoping: For dDocument_Service invoices, leave the `sap_item_code` completely EMPTY (null). Only populate `gl_account` and `sac_entry` if known.
"""

_CURRENT_AI_SYSTEM_PROMPT = _DEFAULT_AI_SYSTEM_PROMPT


def get_ai_system_prompt() -> str:
    return _CURRENT_AI_SYSTEM_PROMPT


def set_ai_system_prompt(prompt: str) -> None:
    global _CURRENT_AI_SYSTEM_PROMPT
    _CURRENT_AI_SYSTEM_PROMPT = prompt


class OcrPreview(BaseModel):
    upload_id: UUID
    filename: str
    extraction_status: str
    message: str
    extracted_text: str = ""
    invoice: StandardInvoice | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
    previous_post_found: bool = False
    previous_doc_id: str | None = None
    previous_invoice_number: str | None = None


class UploadStore:
    def __init__(self, data_dir: Path, repository=None) -> None:
        self.upload_dir = data_dir / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        if repository:
            self.repository = repository
        else:
            from app.repository import DatabaseRepository
            self.repository = DatabaseRepository(
                database_path=data_dir / "invoices.db",
                db_engine=settings.db_engine,
                mysql_host=settings.mysql_host,
                mysql_port=settings.mysql_port,
                mysql_database=settings.mysql_database,
                mysql_user=settings.mysql_user,
                mysql_password=settings.mysql_password,
                mssql_host=settings.mssql_host,
                mssql_port=settings.mssql_port,
                mssql_database=settings.mssql_database,
                mssql_user=settings.mssql_user,
                mssql_password=settings.mssql_password,
            )

    async def save_and_extract(self, file: UploadFile) -> OcrPreview:
        content_type = file.content_type or ""
        if content_type not in SUPPORTED_TYPES:
            suffix = Path(file.filename or "").suffix.lower()
            if suffix == ".pdf":
                content_type = "application/pdf"
            elif suffix in {".png", ".jpg", ".jpeg", ".tiff", ".tif"}:
                content_type = f"image/{suffix.lstrip('.')}" if suffix != ".tif" else "image/tiff"

        if content_type not in SUPPORTED_TYPES:
            raise HTTPException(415, "Upload a PDF, PNG, JPEG, or TIFF invoice.")
        content = await file.read()
        if not content:
            raise HTTPException(400, "The uploaded file is empty.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The file exceeds the 20 MB upload limit.")
        upload_id = uuid4()
        filename = Path(file.filename or "invoice").name
        path = self.upload_dir / f"{upload_id}{Path(filename).suffix.lower()}"
        path.write_bytes(content)
        text, status, message = extract_text(path, content_type)
        
        raw_pdf_text = ""
        try:
            if str(path).lower().endswith(".pdf"):
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                raw_pdf_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        except Exception as e:
            print(f"PyPDF extraction error: {e}")

        invoice, confidence = build_invoice_candidate(text, filename, raw_pdf_text)
        invoice_payload = invoice.model_dump_json() if invoice else ""

        # ── Vendor Matching & Field Auto-Population from Existing Records ──────
        previous_post_found = False
        previous_doc_id: str | None = None
        previous_invoice_number: str | None = None

        if invoice:
            try:
                from app.services import InvoiceService
                from app.master_data import DemoSapMasterDataGateway

                svc = InvoiceService(self.repository, DemoSapMasterDataGateway(self.repository))

                # Resolve CardCode for the extracted vendor
                inv_header = invoice.invoice_header
                card_code = invoice.supplier.sap_card_code
                if not card_code:
                    v_match = svc.master_data.find_vendor_advanced(
                        inv_header.supplier_name, inv_header.supplier_gstin
                    )
                    if v_match:
                        card_code = v_match.card_code
                        invoice.supplier.sap_card_code = card_code
                        invoice.supplier.confidence = v_match.confidence
                        invoice.supplier.reason = v_match.reason

                if card_code:
                    from app.models import InvoiceDocument
                    temp_doc = InvoiceDocument(invoice=invoice, source_filename=filename)
                    enriched_doc, found, prev_id = svc.enrich_document_from_vendor_history(
                        document=temp_doc,
                        card_code=card_code,
                        invoice_number=inv_header.invoice_number,
                    )
                    invoice = enriched_doc.invoice
                    previous_post_found = found
                    previous_doc_id = prev_id
                    previous_invoice_number = inv_header.invoice_number
                    invoice_payload = invoice.model_dump_json()
                    message = (
                        message
                        + f" | ✅ Auto-filled fields from vendor records (CardCode: {card_code})"
                    )
            except Exception as enrich_err:
                print(f"[-] Pre-population enrichment error (non-fatal): {enrich_err}")

        if self.repository.db_engine == "mysql":
            sql = "INSERT INTO uploads (upload_id, filename, stored_path, content_type, extraction_status, extracted_text, invoice_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE filename=VALUES(filename)"
        else:
            sql = "INSERT INTO uploads (upload_id, filename, stored_path, content_type, extraction_status, extracted_text, invoice_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"

        self.repository._execute_sql(
            sql,
            (str(upload_id), filename, str(path), content_type, status, text, invoice_payload, date.today().isoformat()),
        )
        return OcrPreview(
            upload_id=upload_id,
            filename=filename,
            extraction_status=status,
            message=message,
            extracted_text=text[:12000],
            invoice=invoice,
            field_confidence=confidence,
            previous_post_found=previous_post_found,
            previous_doc_id=previous_doc_id,
            previous_invoice_number=previous_invoice_number,
        )

    def get_candidate(self, upload_id: UUID) -> dict[str, str] | None:
        rows = self.repository._execute_sql("SELECT extracted_text, invoice_payload, filename, extraction_status, stored_path, content_type FROM uploads WHERE upload_id = ?", (str(upload_id),))
        if not rows:
            return None
        extracted_text, invoice_payload, filename, extraction_status, stored_path, content_type = rows[0]
        return {
            "extracted_text": extracted_text,
            "invoice_payload": json.loads(invoice_payload) if invoice_payload else None,
            "filename": filename,
            "extraction_status": extraction_status,
            "stored_path": stored_path,
            "content_type": content_type,
        }

    def save_correction(self, upload_id: UUID, invoice: StandardInvoice) -> OcrPreview:
        rows = self.repository._execute_sql("SELECT extracted_text, filename, content_type FROM uploads WHERE upload_id = ?", (str(upload_id),))
        if not rows:
            raise HTTPException(404, "Upload not found")
        extracted_text, filename, content_type = rows[0]
        invoice_payload = invoice.model_dump_json()
        corrected_status = "CORRECTED"
        self.repository._execute_sql(
            "UPDATE uploads SET invoice_payload = ?, extraction_status = ? WHERE upload_id = ?",
            (invoice_payload, corrected_status, str(upload_id)),
        )
        return OcrPreview(
            upload_id=upload_id,
            filename=filename,
            extraction_status=corrected_status,
            message="Manual corrections saved and recorded for training.",
            extracted_text=extracted_text[:12000],
            invoice=invoice,
            field_confidence=invoice.field_confidence,
        )


def extract_text(path: Path, content_type: str) -> tuple[str, str, str]:
    # 1. Primary Engine: NVIDIA Llama 3.2 Vision OCR
    try:
        from PIL import Image
        from app.vision_ocr import vision_engine

        img_to_process = None
        if content_type == "application/pdf":
            images = extract_images_from_pdf(path)
            if images:
                img_to_process = images[0]
            else:
                from PIL import ImageSequence
                image = Image.open(path)
                images = [page.copy() for page in ImageSequence.Iterator(image)]
                if images:
                    img_to_process = images[0]
        else:
            img_to_process = Image.open(path)

        if img_to_process:
            vision_result = vision_engine.extract_from_image(img_to_process)
            if vision_result and isinstance(vision_result, dict):
                json_str = json.dumps(vision_result, indent=2)
                return json_str, "EXTRACTED", "Text and structured data extracted exclusively using NVIDIA Llama 3.2 Vision OCR Engine."
    except Exception as err:
        print(f"[-] NVIDIA Llama Vision AI engine error: {err}")

    # 2. PDF Digital Text Layer Reader
    if content_type == "application/pdf":
        from pypdf import PdfReader
        try:
            pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip()
            if pdf_text:
                return pdf_text, "EXTRACTED", "Text extracted from PDF digital layer."
        except Exception:
            pass

    return "", "OCR_REQUIRED", "No text detected by NVIDIA Llama Vision engine. Please review or upload a clearer scan."



def amount(value: str | int | float | None) -> Decimal:
    cleaned = str(value or "0").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def extract_images_from_pdf(path: Path) -> list["Image.Image"]:
    try:
        import pypdfium2
        images = []
        pdf = pypdfium2.PdfDocument(str(path))
        for i in range(len(pdf)):
            page = pdf[i]
            # scale=2.0 roughly gives 144 DPI which is good enough for OCR without blowing up size
            pil_image = page.render(scale=2.0).to_pil().convert("RGB")
            images.append(pil_image)
        return images
    except Exception as err:
        print(f"[-] Error extracting images from PDF {path} using pypdfium2: {err}")
        return []







def build_invoice_candidate(text: str, filename: str, raw_pdf_text: str = "") -> tuple[StandardInvoice, dict[str, float]]:
    # Check if text contains structured Vision AI JSON
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            v_data = json.loads(json_match.group(0))
            raw_supplier = str(v_data.get("supplier_name") or "UNREADABLE_SUPPLIER")
            supplier_name = raw_supplier.split("/")[0].strip() if "/" in raw_supplier else raw_supplier.strip()
            
            if "LLM APPLIANCES" in supplier_name.upper():
                vendor_name_found = None
                lines = raw_pdf_text.split("\n") if raw_pdf_text else []
                # Fallback to simple keyword logic if we don't have text
                if not lines:
                    supplier_name = "UNREADABLE_SUPPLIER"
                else:
                    for line in lines[:15]:
                        if any(kw in line.upper() for kw in ["V.K.V", "VKV", "INDUSTRIES", "PRIVATE", "LIMITED", "LABOUR BILL"]):
                            if "LLM APPLIANCES" not in line.upper():
                                vendor_name_found = line.strip()
                                break
                    supplier_name = vendor_name_found if vendor_name_found else "UNREADABLE_SUPPLIER"
                    
            supplier_gstin = v_data.get("supplier_gstin")
            if supplier_gstin and "AAACL1900F" in supplier_gstin.upper():
                supplier_gstin = None
            inv_no = str(v_data.get("invoice_number") or v_data.get("vendor_ref_no") or "INV-PENDING")
            inv_date_str = str(v_data.get("invoice_date") or date.today().isoformat())
            comments_text = str(v_data.get("narration") or "Created through AI OCR Automation")


            invoice_date = date.today()
            print(f"DEBUG: parsing inv_date_str={inv_date_str}")
            if inv_date_str:
                from datetime import datetime
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
                    try:
                        invoice_date = datetime.strptime(inv_date_str, fmt).date()
                        print(f"DEBUG: matched format {fmt}, invoice_date={invoice_date}")
                        break
                    except ValueError:
                        pass

            # Parse tax rates (CGST, SGST, IGST)
            cgst_rate = amount(v_data.get("cgst_rate"))
            sgst_rate = amount(v_data.get("sgst_rate"))
            igst_rate = amount(v_data.get("igst_rate"))
            parsed_tax_pct = amount(v_data.get("tax_percentage"))
            if parsed_tax_pct > Decimal("100"): parsed_tax_pct = Decimal("100")
            if parsed_tax_pct < Decimal("0"): parsed_tax_pct = Decimal("0")

            header_grand_total = amount(v_data.get("grand_total"))
            header_subtotal = amount(v_data.get("subtotal"))
            header_tax_amount = amount(v_data.get("tax_amount"))

            # If there is no Supplier GSTIN (e.g. internal claim voucher, payment voucher, or unregistered vendor),
            # there should be absolutely no GST. Force all tax to 0 to prevent OCR hallucinations.
            if not supplier_gstin:
                cgst_rate = Decimal("0.00")
                sgst_rate = Decimal("0.00")
                igst_rate = Decimal("0.00")
                parsed_tax_pct = Decimal("0.00")
                header_tax_amount = Decimal("0.00")

            if parsed_tax_pct == Decimal("0.00"):
                if cgst_rate > Decimal("0") or sgst_rate > Decimal("0"):
                    parsed_tax_pct = cgst_rate + sgst_rate
                elif igst_rate > Decimal("0"):
                    parsed_tax_pct = igst_rate
                elif header_tax_amount > Decimal("0") and header_subtotal > Decimal("0"):
                    parsed_tax_pct = ((header_tax_amount / header_subtotal) * Decimal("100")).quantize(Decimal("0.01"))
                    if parsed_tax_pct > Decimal("100"): parsed_tax_pct = Decimal("100")
            
            # Fallback: sanity check raw text with regex if tax is 0 or 100
            if (parsed_tax_pct == Decimal("0.00") or parsed_tax_pct >= Decimal("50.00")) and raw_pdf_text:
                cgst_match = re.search(r"CGST[^%]*?(\d+(?:\.\d+)?)\s*%", raw_pdf_text, re.IGNORECASE)
                sgst_match = re.search(r"SGST[^%]*?(\d+(?:\.\d+)?)\s*%", raw_pdf_text, re.IGNORECASE)
                if cgst_match and sgst_match:
                    parsed_tax_pct = Decimal(cgst_match.group(1)) + Decimal(sgst_match.group(1))

            # Determine SAP tax_code from percentage
            def determine_tax_code(pct: Decimal, explicit_code: str | None = None) -> str:
                if explicit_code and explicit_code.strip():
                    return explicit_code.strip()
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

            default_tax_code = determine_tax_code(parsed_tax_pct, v_data.get("tax_code"))

            # Extract lines from Vision JSON
            parsed_lines = []
            calculated_subtotal = Decimal("0.00")
            calculated_tax_total = Decimal("0.00")
            v_lines = v_data.get("lines") or []

            for idx, item in enumerate(v_lines, start=1):
                desc = str(item.get("description") or f"Line {idx}")
                qty = amount(item.get("quantity") or 1.0)
                price = amount(item.get("unit_price") or item.get("line_total") or 0.0)
                line_subtotal = amount(item.get("line_total") or (qty * price))
                calculated_subtotal += line_subtotal

                line_tax_pct = amount(item.get("tax_percentage") or parsed_tax_pct)
                if line_tax_pct > Decimal("100"): line_tax_pct = Decimal("100")
                if line_tax_pct < Decimal("0"): line_tax_pct = Decimal("0")
                
                line_tax_amt = amount(item.get("tax_amount"))
                if line_tax_amt == Decimal("0.00") and line_tax_pct > Decimal("0"):
                    line_tax_amt = ((line_subtotal * line_tax_pct) / Decimal("100")).quantize(Decimal("0.01"))
                calculated_tax_total += line_tax_amt

                line_tax_code = determine_tax_code(line_tax_pct, item.get("tax_code") or default_tax_code)

                parsed_lines.append(
                    InvoiceLine(
                        line_number=idx,
                        description=desc,
                        quantity=qty if qty > 0 else Decimal("1.00"),
                        unit_price=price.quantize(Decimal("0.01")),
                        tax_percentage=line_tax_pct.quantize(Decimal("0.01")),
                        tax_amount=line_tax_amt.quantize(Decimal("0.01")),
                        line_total=line_subtotal.quantize(Decimal("0.01")),
                        sap_item_code=None,
                        gl_account=None,
                        tax_code=line_tax_code,
                        mapping_confidence=95.0,
                    )
                )

            final_subtotal = (header_subtotal if header_subtotal > Decimal("0") else calculated_subtotal).quantize(Decimal("0.01"))
            final_tax_amount = (header_tax_amount if header_tax_amount > Decimal("0") else calculated_tax_total).quantize(Decimal("0.01"))
            final_grand_total = (header_grand_total if header_grand_total > Decimal("0") else (final_subtotal + final_tax_amount)).quantize(Decimal("0.01"))
            matched_card_code = None
            matched_reason = "Vision AI extraction"


            candidate = StandardInvoice(
                invoice_header=InvoiceHeader(
                    supplier_name=supplier_name,
                    supplier_gstin=supplier_gstin,
                    invoice_number=inv_no,
                    vendor_ref_no=inv_no,
                    invoice_date=invoice_date,
                    document_date=invoice_date,
                    posting_date=date.today(),
                    due_date=invoice_date,
                    local_currency="INR",
                    series=521,
                    bpl_id_assigned_to_invoice=1,
                    transaction_type="gsttrantyp_BillOfSupply",
                    comments=comments_text,
                ),
                supplier=SupplierMapping(sap_card_code=matched_card_code, confidence=95.0, reason=matched_reason),

                lines=parsed_lines if parsed_lines else [
                    InvoiceLine(
                        line_number=1,
                        description="Expense Payment Voucher",
                        quantity=Decimal("1.00"),
                        unit_price=final_subtotal,
                        tax_percentage=parsed_tax_pct,
                        tax_amount=final_tax_amount,
                        line_total=final_subtotal,
                        tax_code=default_tax_code,
                        mapping_confidence=95.0,
                    )
                ],
                totals=InvoiceTotals(
                    subtotal=final_subtotal,
                    tax_amount=final_tax_amount,
                    grand_total=final_grand_total,
                ),
            )


            confidences = {
                "supplier_name": 95.0,
                "supplier_gstin": 50.0,
                "invoice_number": 95.0,
                "invoice_date": 95.0,
                "grand_total": 95.0,
            }
            return candidate, confidences
        except Exception as err:
            print(f"[-] Error parsing Vision AI JSON response: {err}")

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    gstin_matches = re.findall(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]\b", text.upper())
    
    # Exclude buyer GSTIN (33AAACL1900F1Z9) if present
    supplier_gstin = None
    for g in gstin_matches:
        if "AAACL1900F" not in g:
            supplier_gstin = g
            break
    if not supplier_gstin and gstin_matches:
        supplier_gstin = gstin_matches[0]

    invoice_match = re.search(r"(?im)(?:tax\s+invoice\s*(?:no\.?|number)?|invoice\s*(?:no\.?|number)?|bill\s*no\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})", text)
    date_match = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text)
    
    # Extract Net Amount / Grand Total (e.g. 10181.00)
    grand_total_match = re.search(r"(?im)(?:net\s*amount|total\s*amt\.?\s*after\s*tax|grand\s*total|invoice\s*total)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)", text)
    
    # Extract Line Subtotal / Amount before tax (e.g. 9090.00)
    subtotal_match = re.search(r"(?im)(?:total\s*amount|amount|sub\s*total)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)", text)

    # Detect Tax Rates (e.g. CGST 6% + SGST 6% -> 12% total, or GST 18%)
    cgst_match = re.search(r"CGST[^%]*?(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    sgst_match = re.search(r"SGST[^%]*?(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    
    tax_pct = Decimal("0")
    tax_code = "IGST0"

    if cgst_match and sgst_match:
        cgst_rate = Decimal(cgst_match.group(1))
        sgst_rate = Decimal(sgst_match.group(1))
        tax_pct = cgst_rate + sgst_rate
        if tax_pct > Decimal("100"): tax_pct = Decimal("100")
        if tax_pct < Decimal("0"): tax_pct = Decimal("0")
        tax_code = f"GST{int(tax_pct)}"
    elif re.search(r"18\s*%", text):
        tax_pct = Decimal("18")
        tax_code = "GST18"

    # Try finding vendor name keywords
    vendor_name_found = None
    for line in lines[:10]:
        if any(kw in line.upper() for kw in ["V.K.V", "VKV", "INDUSTRIES", "PRIVATE", "LIMITED", "LABOUR BILL"]):
            if "LLM APPLIANCES" not in line.upper():
                vendor_name_found = line
                break
    
    if not vendor_name_found and lines:
        vendor_name_found = lines[0][:120]

    supplier_name = vendor_name_found if vendor_name_found else "UNREADABLE_SUPPLIER"
    inv_no = invoice_match.group(1) if invoice_match else "INV-PENDING"

    invoice_date = date.today()
    if date_match:
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y"):
            try:
                from datetime import datetime
                invoice_date = datetime.strptime(date_match.group(1), fmt).date()
                break
            except ValueError:
                continue

    extracted_grand_total = amount(grand_total_match.group(1) if grand_total_match else None)
    extracted_subtotal = amount(subtotal_match.group(1) if subtotal_match else None)

    if extracted_grand_total > Decimal("0"):
        grand_total_val = extracted_grand_total
        if extracted_subtotal > Decimal("0") and extracted_subtotal <= grand_total_val:
            line_subtotal_val = extracted_subtotal
        elif tax_pct > Decimal("0"):
            line_subtotal_val = (grand_total_val / (Decimal("1") + (tax_pct / Decimal("100")))).quantize(Decimal("0.01"))
        else:
            line_subtotal_val = grand_total_val
    elif extracted_subtotal > Decimal("0"):
        line_subtotal_val = extracted_subtotal
        grand_total_val = line_subtotal_val
    else:
        grand_total_val = Decimal("0.00")
        line_subtotal_val = Decimal("0.00")

    tax_amt_val = max(Decimal("0.00"), grand_total_val - line_subtotal_val)

    # Master Data mapping prediction for card_code
    card_code = None
    if "V.K.V" in supplier_name.upper() or (supplier_gstin and "AHYPS1047" in supplier_gstin):
        card_code = "V00101"
    elif supplier_gstin:
        card_code = None

    candidate = StandardInvoice(
        invoice_header=InvoiceHeader(
            supplier_name=supplier_name,
            supplier_gstin=supplier_gstin,
            invoice_number=inv_no,
            vendor_ref_no=inv_no,
            invoice_date=invoice_date,
            document_date=invoice_date,
            posting_date=date.today(),
            due_date=invoice_date,
            local_currency="INR",
            series=521,
            bpl_id_assigned_to_invoice=1,
            transaction_type="gsttrantyp_GSTTaxInvoice" if tax_pct > Decimal("0") else "gsttrantyp_BillOfSupply",
            comments="Created through AI OCR Automation",
        ),
        supplier=SupplierMapping(sap_card_code=card_code, confidence=99.8 if (supplier_gstin and card_code) else 50.0, reason="OCR extraction & master data candidate"),
        lines=[
            InvoiceLine(
                line_number=1,
                description=lines[1][:100] if len(lines) > 1 else (supplier_name if supplier_name != "UNREADABLE_SUPPLIER" else "Document Purchase"),
                quantity=Decimal("1.00"),
                unit_price=line_subtotal_val,
                tax_percentage=tax_pct,
                tax_amount=tax_amt_val,
                line_total=line_subtotal_val,
                sap_item_code=None,
                gl_account=None,
                tax_code=tax_code,
                mapping_confidence=50.0,
            )
        ],
        totals=InvoiceTotals(
            subtotal=line_subtotal_val,
            tax_amount=tax_amt_val,
            grand_total=grand_total_val,
        ),
    )

    confidences = {
        "supplier_name": 90.0 if vendor_name_found else 20.0,
        "supplier_gstin": 98.0 if supplier_gstin else 10.0,
        "invoice_number": 90.0 if invoice_match else 20.0,
        "invoice_date": 90.0 if date_match else 50.0,
        "grand_total": 90.0 if extracted_grand_total > Decimal("0") else 20.0,
    }

    return candidate, confidences
