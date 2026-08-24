"""SAP Business One Service Layer client.

Flow (matching 'SAP Login API Sample' and 'AP service Invoice Posting' files):
  1. POST /b1s/v1/Login  → receive SessionId in JSON response
  2. Use SessionId as B1SESSION cookie for all subsequent API calls
  3. POST /b1s/v2/PurchaseInvoices  → create AP Invoice
  4. POST /b1s/v1/Logout → release session slot
"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests
from urllib3.exceptions import InsecureRequestWarning

from .settings import settings

# Suppress SSL warnings for corporate self-signed certificates
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

log = logging.getLogger(__name__)


class SapClientError(RuntimeError):
    pass


# Fields NOT accepted by SAP PurchaseInvoices POST endpoint — strip before sending
_SAP_READONLY_FIELDS = {
    "DocObjectCode",  # output-only field
    "ProjectCode",    # SAP rejects default "NONE" project code string
}

# Minimal required fields matching the working curl sample
_SAP_MINIMAL_LINE_FIELDS = {
    "LineNum", "SACEntry", "Price", "UnitPrice",
    "AccountCode", "TaxCode", "LocationCode",
    "CostingCode", "CostingCode2", "CostingCode3",
}


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove read-only / SAP-rejected fields from the posting payload."""
    cleaned = {k: v for k, v in payload.items() if k not in _SAP_READONLY_FIELDS and v is not None}
    # Clean DocumentLines too
    if "DocumentLines" in cleaned:
        cleaned_lines = []
        for line in cleaned["DocumentLines"]:
            cleaned_line = {}
            for k, v in line.items():
                if k in _SAP_READONLY_FIELDS or v is None or (v == "NONE" and k == "ProjectCode"):
                    continue
                cleaned_line[k] = v
            cleaned_lines.append(cleaned_line)
        cleaned["DocumentLines"] = cleaned_lines
    return cleaned


def login_and_get_session() -> tuple[requests.Session, str]:
    """
    Login to SAP Service Layer via v1/Login.
    Returns (requests.Session with B1SESSION cookie, SessionId string).

    Matches SAP Login API Sample:
      POST https://rain.rfgb.net:50000/b1s/v1/Login
      Body: { "CompanyDB": "...", "UserName": "...", "Password": "..." }
      Response: { "SessionId": "...", "Version": "...", "SessionTimeout": 30 }
    """
    if not settings.base_url:
        raise SapClientError("SAP Service Layer base URL not configured in .env")

    session = requests.Session()
    login_body = {
        "CompanyDB": settings.company_db,
        "UserName": settings.username,
        "Password": settings.password,
    }

    log.info("SAP Login: POST %s", settings.login_url)
    try:
        resp = session.post(
            settings.login_url,
            json=login_body,
            headers={"Content-Type": "application/json"},
            verify=False,   # corporate self-signed cert
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise SapClientError(f"SAP Login failed: {exc}") from exc

    data = resp.json()
    session_id = data.get("SessionId", "")
    if not session_id:
        raise SapClientError(f"SAP Login response did not contain SessionId. Response: {data}")

    log.info("SAP Login OK — SessionId: %s...", session_id[:16])

    # Set the B1SESSION cookie on the session so all subsequent calls include it
    # (SAP Service Layer uses cookie-based session auth)
    session.cookies.set("B1SESSION", session_id)

    return session, session_id


def logout(session: requests.Session) -> None:
    """POST to /b1s/v1/Logout to cleanly release the session slot."""
    try:
        session.post(settings.logout_url, verify=False, timeout=10)
        log.info("SAP Logout OK")
    except Exception:
        pass  # Best-effort logout


def upload_attachment_to_sap(session: requests.Session, attachment_info: dict[str, str]) -> int | None:
    """Upload document file copy to SAP Attachments2 endpoint and return AbsoluteEntry ID."""
    from pathlib import Path

    import uuid

    filename = attachment_info.get("filename")
    file_path = attachment_info.get("path")

    if not file_path or not Path(file_path).exists() or not filename:
        return None
        
    # SAP requires unique filenames. Prefix with a short UUID to guarantee uniqueness.
    unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"

    attachments_url = f"{settings.base_url.rstrip('/')}/Attachments2"
    log.info("SAP Uploading attachment: POST %s | file=%s", attachments_url, unique_filename)

    try:
        with open(file_path, "rb") as f:
            files = {"files": (unique_filename, f, "application/octet-stream")}
            resp = session.post(
                attachments_url,
                files=files,
                verify=False,
                timeout=30,
            )
        if resp.ok:
            data = resp.json()
            entry = data.get("AbsoluteEntry")
            log.info("SAP Attachments2 upload OK — AbsoluteEntry=%s", entry)
            return entry
        else:
            log.warning("SAP Attachments2 upload returned %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        log.warning("Failed to upload attachment to SAP: %s", exc)

    return None


def post_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Post AP Invoice to SAP Business One PurchaseInvoices endpoint.

    Steps:
      1. Login → get SessionId
      2. Upload source document attachment to /b1s/v1/Attachments2 (if present)
      3. Clean payload (remove read-only fields & link AttachmentEntry)
      4. POST to /b1s/v2/PurchaseInvoices with B1SESSION cookie
      5. Logout
    """
    if not settings.posting_enabled:
        raise SapClientError("SAP posting is disabled (set SAP_POSTING_ENABLED=true in .env)")

    # Step 1: Login
    session, session_id = login_and_get_session()

    try:
        attachment_info = payload.pop("AttachmentFile", None)

        # Step 2: Clean payload — remove fields SAP rejects on POST
        clean = _clean_payload(payload)

        # Step 3: Upload attachment if file is present
        if attachment_info:
            entry = upload_attachment_to_sap(session, attachment_info)
            if entry is not None:
                clean["AttachmentEntry"] = entry

        # Step 4: POST to PurchaseInvoices (v2) or Drafts based on setting
        is_draft = settings.posting_mode == "draft"
        url = settings.drafts_url if is_draft else settings.purchase_invoices_url
        if is_draft:
            clean["DocObjectCode"] = "oPurchaseInvoices"

        log.info("SAP POST %s: %s | CardCode=%s | NumAtCard=%s | AttachmentEntry=%s",
                 "Drafts" if is_draft else "PurchaseInvoices",
                 url, clean.get("CardCode"), clean.get("NumAtCard"), clean.get("AttachmentEntry"))

        resp = session.post(
            url,
            json=clean,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            verify=False,
            timeout=30,
        )
        if not resp.ok:
            # Surface the SAP error body for debugging
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            raise SapClientError(
                f"{resp.status_code} {resp.reason} — SAP Error: {err_body}"
            )
        result = resp.json()
        log.info("SAP %s POST OK — DocEntry=%s DocNum=%s",
                 "Draft" if is_draft else "PurchaseInvoices",
                 result.get("DocEntry"), result.get("DocNum"))
        return result
    except SapClientError:
        raise
    except Exception as exc:
        raise SapClientError(f"Failed to post to SAP PurchaseInvoices: {exc}") from exc
    finally:
        # Step 5: Logout
        logout(session)


def _fetch_all_odata_pages(session: requests.Session, initial_url: str) -> list[dict[str, Any]]:
    """Loop through OData pagination (odata.nextLink) and return all records."""
    from urllib.parse import urljoin
    results = []
    base_endpoint = f"{settings.base_url.rstrip('/')}/"
    next_url = initial_url

    while next_url:
        try:
            resp = session.get(next_url, verify=False, timeout=25)
            if not resp.ok:
                log.warning("OData fetch warning (%s) for URL %s: %s", resp.status_code, next_url, resp.text[:200])
                break
            data = resp.json()
            vals = data.get("value", [])
            results.extend(vals)
            next_link = data.get("odata.nextLink")
            if next_link:
                next_url = urljoin(base_endpoint, next_link)
            else:
                next_url = None
        except Exception as exc:
            log.warning("OData pagination exception: %s", exc)
            break

    return results


def get_open_purchase_orders(card_code: str) -> list[dict[str, Any]]:
    """Fetch open Purchase Orders for a specific vendor from SAP."""
    if not settings.posting_enabled or not settings.base_url:
        return []
        
    session, session_id = login_and_get_session()
    try:
        url = f"{settings.base_url.rstrip('/')}/PurchaseOrders?$filter=CardCode eq '{card_code}' and DocumentStatus eq 'bost_Open'&$select=DocEntry,DocNum,DocDate,DocTotal,DocumentLines"
        log.info("SAP GET Open PurchaseOrders: %s", url)
        
        resp = session.get(url, verify=False, timeout=30)
        if not resp.ok:
            log.warning("SAP GET Open PurchaseOrders returned %s: %s", resp.status_code, resp.text)
            return []
            
        data = resp.json()
        return data.get("value", [])
    except Exception as exc:
        log.warning("Failed to fetch open POs from SAP: %s", exc)
        return []
    finally:
        logout(session)


def _fetch_sql_query_from_sap(session: requests.Session, sql_name: str, sql_text: str) -> list[dict[str, Any]]:
    """Create and execute a raw SQL query using SAP Service Layer."""
    url = f"{settings.base_url.rstrip('/')}/SQLQueries"
    payload = {
        "SqlCode": sql_name,
        "SqlName": sql_name,
        "SqlText": sql_text
    }
    
    # Try to create or update the query
    res = session.post(url, json=payload, verify=settings.verify_tls)
    if not res.ok and "already exists" not in res.text:
        log.error(f"Failed to create SQL query {sql_name}: {res.text}")
        # Sometimes we might want to PATCH it if we need to update, but assuming it's static
        
    # Execute the query
    exec_url = f"{url}('{sql_name}')/List"
    return _fetch_all_odata_pages(session, exec_url)


def get_all_open_documents_from_sap(doc_type: str = "PO") -> list[dict[str, Any]]:
    """Fetch all open Purchase Orders or GRNs from SAP using custom SQL."""
    if not settings.posting_enabled or not settings.base_url:
        return []
        
    session, session_id = login_and_get_session()
    try:
        if doc_type == "PO":
            sql_name = "ap_ocr_open_po_v2"
            table_h = "OPOR"
            table_l = "POR1"
        else:
            sql_name = "ap_ocr_open_grn_v2"
            table_h = "OPDN"
            table_l = "PDN1"
            
        sql_text = f"""
            SELECT T0."DocNum", T0."DocEntry", T0."DocDate", T0."DocDueDate", T0."TaxDate", T0."DocTotal",
            T0."CardCode", T0."CardName", T0."Series", T0."BPLId", T1."LineNum", T1."ItemCode", T1."Dscription",
            T1."Quantity", T1."PriceBefDi", T1."LineTotal", T1."VatSum", T1."WhsCode", T1."AcctCode",
            T1."VatGroup", T1."TaxCode", T1."OcrCode", T1."OcrCode2", T1."OcrCode3", T1."HsnEntry", T1."LocCode"
            FROM {table_h} T0 
            INNER JOIN {table_l} T1 ON T0."DocEntry" = T1."DocEntry" 
            WHERE T0."CANCELED" = 'N' AND T1."LineStatus" = 'O'
            ORDER BY T0."DocNum"
        """
        
        log.info(f"SAP SQL GET All Open {doc_type}s")
        flat_results = _fetch_sql_query_from_sap(session, sql_name, sql_text)
        
        # Group flat results into nested document structure
        docs = {}
        for row in flat_results:
            doc_num = row.get("DocNum")
            if doc_num not in docs:
                docs[doc_num] = {
                    "DocEntry": row.get("DocEntry"),
                    "DocNum": doc_num,
                    "DocDate": row.get("DocDate"),
                    "DocDueDate": row.get("DocDueDate"),
                    "TaxDate": row.get("TaxDate"),
                    "DocTotal": row.get("DocTotal"),
                    "CardCode": row.get("CardCode"),
                    "CardName": row.get("CardName"),
                    "Series": row.get("Series"),
                    "BPL_IDAssignedToInvoice": row.get("BPLId"),
                    "DocumentLines": []
                }
            docs[doc_num]["DocumentLines"].append({
                "LineNum": row.get("LineNum"),
                "ItemCode": row.get("ItemCode"),
                "ItemDescription": row.get("Dscription"),
                "Quantity": row.get("Quantity"),
                "UnitPrice": row.get("PriceBefDi"),
                "LineTotal": row.get("LineTotal"),
                "TaxTotal": row.get("VatSum"),
                "WarehouseCode": row.get("WhsCode"),
                "AccountCode": row.get("AcctCode"),
                "TaxCode": row.get("TaxCode") or row.get("VatGroup"),
                "CostingCode": row.get("OcrCode"),
                "CostingCode2": row.get("OcrCode2"),
                "CostingCode3": row.get("OcrCode3"),
                "SacEntry": row.get("HsnEntry"),
                "LocationCode": row.get("LocCode")
            })
            
        return list(docs.values())
        
    except Exception as exc:
        log.warning("Failed to fetch all open %s from SAP: %s", doc_type, exc)
        return []
    finally:
        logout(session)


def sync_all_master_data_from_sap(target_category: str | None = None) -> list[dict[str, Any]]:
    """
    Connect live to SAP Business One Service Layer and fetch active master data.
    If target_category is specified (e.g. 'accounts' or 'vendors'), only fetches that category.
    Returns list of normalized dicts: [{"category": ..., "code": ..., "name": ..., "extra_data": ...}]
    """
    session, _ = login_and_get_session()
    base = settings.base_url.rstrip("/")
    master_records: list[dict[str, Any]] = []

    target = target_category.strip().lower() if target_category and target_category.strip() and target_category != "all" else None

    try:
        # 1. Business Partners / Vendors
        if not target or target == "vendors":
            try:
                bp_url = f"{base}/BusinessPartners?$filter=CardType eq 'cSupplier'&$select=CardCode,CardName,FederalTaxID,Currency,BilltoDefault,ShipToDefault,PayTermsGrpCode,BillToState,BPAddresses,SubjectToWithholdingTax,WTCode,BPWithholdingTaxCollection"
                bp_items = _fetch_all_odata_pages(session, bp_url)
                for b in bp_items:
                    code = b.get("CardCode")
                    if code:
                        addresses = b.get("BPAddresses") or []
                        pay_to = b.get("BilltoDefault") or ""
                        ship_to = b.get("ShipToDefault") or ""
                        state = b.get("BillToState") or ""
                        gstin = b.get("FederalTaxID") or ""

                        for addr in addresses:
                            if not pay_to and addr.get("AddressType") == "bo_BillTo":
                                pay_to = addr.get("AddressName") or ""
                            if not ship_to and addr.get("AddressType") == "bo_ShipTo":
                                ship_to = addr.get("AddressName") or ""
                            if not state and addr.get("State"):
                                state = addr.get("State")
                            if not gstin and addr.get("GSTRegnNo"):
                                gstin = addr.get("GSTRegnNo")

                        wtax_sub = b.get("SubjectToWithholdingTax")
                        wtax_liable = "Yes" if wtax_sub in ("boYES", "tYES", "YES", True) else "No"
                        
                        wt_code = str(b.get("WTCode") or "").strip()
                        if not wt_code:
                            wt_coll = b.get("BPWithholdingTaxCollection") or []
                            if wt_coll and isinstance(wt_coll, list) and len(wt_coll) > 0:
                                wt_code = str(wt_coll[0].get("WTCode") or "").strip()
                        if not wt_code and wtax_liable == "Yes":
                            wt_code = "C004"

                        extra_obj = {
                            "gstin": str(gstin or "").strip(),
                            "currency": str(b.get("Currency") or "INR").strip(),
                            "pay_to_code": str(pay_to or "").strip(),
                            "ship_to_code": str(ship_to or "").strip(),
                            "payment_terms": str(b.get("PayTermsGrpCode") if b.get("PayTermsGrpCode") is not None else "-1").strip(),
                            "place_of_supply": str(state or "33-Tamil Nadu").strip(),
                            "wtax_liable": wtax_liable,
                            "wt_code": wt_code or "C004",
                        }
                        master_records.append({
                            "category": "vendors",
                            "code": str(code).strip(),
                            "name": str(b.get("CardName") or code).strip(),
                            "extra_data": json.dumps(extra_obj),
                        })
                log.info("SAP Master Sync: Fetched %d vendors with WTax & 8-field details", len(bp_items))
            except Exception as exc:
                log.warning("SAP master sync BusinessPartners warning: %s", exc)

        # 1.05 Vendor Addresses
        if not target or target == "vendor_addresses":
            try:
                bp_url = f"{base}/BusinessPartners?$filter=CardType eq 'cSupplier'&$select=CardCode,BPAddresses"
                bp_items = _fetch_all_odata_pages(session, bp_url)
                addr_count = 0
                for b in bp_items:
                    code = b.get("CardCode")
                    if code:
                        addresses = b.get("BPAddresses") or []
                        for addr in addresses:
                            addr_name = addr.get("AddressName") or ""
                            if not addr_name: continue
                            
                            extra_obj = {
                                "address_type": "ship" if addr.get("AddressType") == "bo_ShipTo" else "pay",
                                "address_text": addr.get("Street") or "",
                                "block": addr.get("Block") or "",
                                "building": addr.get("BuildingFloorRoom") or "",
                                "street": addr.get("Street") or "",
                                "street_no": addr.get("StreetNo") or "",
                                "city": addr.get("City") or "",
                                "state": addr.get("State") or "",
                                "country": addr.get("Country") or "",
                                "gst_regn_no": addr.get("GSTRegnNo") or ""
                            }
                            
                            master_records.append({
                                "category": "vendor_addresses",
                                "code": f"{code}::{addr_name}",
                                "name": str(code),
                                "extra_data": json.dumps(extra_obj)
                            })
                            addr_count += 1
                log.info("SAP Master Sync: Fetched %d vendor addresses", addr_count)
            except Exception as exc:
                log.warning("SAP master sync Vendor Addresses warning: %s", exc)

        # 1.1 Withholding Tax Codes (WTax Codes)
        if not target or target == "wtax_codes":
            try:
                wt_url = f"{base}/WithholdingTaxCodes?$select=WTCode,WTName,Rate"
                wt_list = _fetch_all_odata_pages(session, wt_url)
                for w in wt_list:
                    wcode = w.get("WTCode")
                    if wcode:
                        master_records.append({
                            "category": "wtax_codes",
                            "code": str(wcode).strip(),
                            "name": str(w.get("WTName") or wcode).strip(),
                            "extra_data": str(w.get("Rate") if w.get("Rate") is not None else "1.0"),
                        })
                log.info("SAP Master Sync: Fetched %d WTax codes", len(wt_list))
            except Exception as exc:
                log.warning("SAP master sync WithholdingTaxCodes warning: %s", exc)

        # 2. Items
        if not target or target == "items":
            try:
                item_url = f"{base}/Items?$select=ItemCode,ItemName"
                item_list = _fetch_all_odata_pages(session, item_url)
                for i in item_list:
                    code = i.get("ItemCode")
                    if code:
                        master_records.append({
                            "category": "items",
                            "code": str(code).strip(),
                            "name": str(i.get("ItemName") or code).strip(),
                            "extra_data": "",
                        })
                log.info("SAP Master Sync: Fetched %d items", len(item_list))
            except Exception as exc:
                log.warning("SAP master sync Items warning: %s", exc)

        # 3. Chart of Accounts (G/L Accounts)
        if not target or target == "accounts":
            try:
                acct_url = f"{base}/ChartOfAccounts?$filter=ActiveAccount eq 'tYES'&$select=Code,Name,FormatCode,AccountType"
                acct_list = _fetch_all_odata_pages(session, acct_url)
                if not acct_list:
                    # Fallback query without ActiveAccount filter if SAP returns empty
                    acct_url = f"{base}/ChartOfAccounts?$select=Code,Name,FormatCode,AccountType"
                    acct_list = _fetch_all_odata_pages(session, acct_url)

                for a in acct_list:
                    code = a.get("Code")
                    if code:
                        master_records.append({
                            "category": "accounts",
                            "code": str(code).strip(),
                            "name": str(a.get("Name") or code).strip(),
                            "extra_data": str(a.get("FormatCode") or "").strip(),
                        })
                log.info("SAP Master Sync: Fetched %d G/L accounts", len(acct_list))
            except Exception as exc:
                log.warning("SAP master sync ChartOfAccounts warning: %s", exc)

        # 4. Tax Codes
        if not target or target == "tax_codes":
            try:
                tax_url = f"{base}/SalesTaxCodes?$select=Code,Name,Rate"
                tax_list = _fetch_all_odata_pages(session, tax_url)
                for t in tax_list:
                    code = t.get("Code")
                    if code:
                        master_records.append({
                            "category": "tax_codes",
                            "code": str(code).strip(),
                            "name": str(t.get("Name") or code).strip(),
                            "extra_data": str(t.get("Rate") if t.get("Rate") is not None else "0"),
                        })
                log.info("SAP Master Sync: Fetched %d tax codes", len(tax_list))
            except Exception as exc:
                log.warning("SAP master sync SalesTaxCodes warning: %s", exc)

        # 5. Profit Centers / Cost Centers
        if not target or target.startswith("cost_centers"):
            try:
                # SAP B1 uses ProfitCenters for Cost Centers across dimensions
                cc_url = f"{base}/ProfitCenters?$select=CenterCode,CenterName,InWhichDimension,Active"
                cc_list = _fetch_all_odata_pages(session, cc_url)
                for d in cc_list:
                    # Check active status if present
                    if d.get("Active") and d.get("Active") != "tYES":
                        continue
                    code = d.get("CenterCode")
                    if code:
                        dim = d.get("InWhichDimension") or d.get("InDimen") or 1
                        cat = f"cost_centers{dim}" if dim in (1, 2, 3, 4, 5) else "cost_centers1"
                        master_records.append({
                            "category": cat,
                            "code": str(code).strip(),
                            "name": str(d.get("CenterName") or code).strip(),
                            "extra_data": "",
                        })
                log.info("SAP Master Sync: Fetched %d cost centers", len(cc_list))
            except Exception as exc:
                log.warning("SAP master sync ProfitCenters warning: %s", exc)
        # 6. Branches / Business Places
        branches_map: dict[str, str] = {}
        if not target or target in ("branches", "series"):
            try:
                bp_url = f"{base}/BusinessPlaces?$select=BPLID,BPLName"
                res = session.get(bp_url, verify=False, timeout=20)
                if res.ok:
                    items = res.json().get("value", [])
                    for b_item in items:
                        bpl_id = b_item.get("BPLID")
                        bpl_name = b_item.get("BPLName")
                        if bpl_id is not None:
                            bpl_id_str = str(bpl_id).strip()
                            bpl_name_str = str(bpl_name or bpl_id_str).strip()
                            branches_map[bpl_id_str] = bpl_name_str
                            if not target or target == "branches":
                                master_records.append({
                                    "category": "branches",
                                    "code": bpl_id_str,
                                    "name": bpl_name_str,
                                    "extra_data": "",
                                })
                    log.info("SAP Master Sync: Fetched %d branches", len(items))
            except Exception as exc:
                log.warning("SAP master sync Branches warning: %s", exc)

        # 7. Document Series for AP Invoices & Credit Memos (DocumentTypes 18, 19, 204)
        if not target or target == "series":
            try:
                
                # Fetch active period indicators to validate series
                active_indicators = set()
                try:
                    fp_url = f"{base}/FinancePeriods?$filter=PeriodStatus eq 'tps_Unlocked'&$select=PeriodIndicator"
                    fp_res = session.get(fp_url, verify=False, timeout=10)
                    if fp_res.ok:
                        for p in fp_res.json().get("value", []):
                            ind = p.get("PeriodIndicator")
                            if ind: active_indicators.add(str(ind).strip())
                except Exception as exc:
                    log.warning("SAP master sync FinancePeriods active check warning: %s", exc)
                
                # Fallback indicator if none found
                if not active_indicators:
                    active_indicators.add("26-27")
                
                # Execute the raw SQL Query provided by the user via SQLQueries endpoint
                series_query_name = "ocr_get_ap_series"
                query_payload = {
                    "SqlCode": series_query_name,
                    "SqlName": "Get Series NNM1",
                    "SqlText": 'SELECT T0."ObjectCode", T0."Series", T0."SeriesName", T0."Indicator", T0."BPLId", T1."BPLName" FROM NNM1 T0 INNER JOIN OBPL T1 ON T0."BPLId" = T1."BPLId" WHERE T0."ObjectCode" = \'18\''
                }
                
                # 1. Try to create the query if it doesn't exist
                session.post(f"{base}/SQLQueries", json=query_payload, verify=False, timeout=10)
                
                list_url = f"{base}/SQLQueries('{series_query_name}')/List"
                total_series_count = 0
                
                while list_url:
                    res = session.get(list_url, verify=False, timeout=30)
                    if not res.ok:
                        log.warning(f"Failed to execute series query at {list_url}: {res.text}")
                        break
                        
                    data = res.json()
                    items = data.get("value", [])
                    total_series_count += len(items)
                    
                    for s_item in items:
                        s_code = s_item.get("Series")
                        s_name = s_item.get("SeriesName")
                        bpl_id = s_item.get("BPLId")
                        indicator = s_item.get("Indicator") or ""
                        bpl_name = s_item.get("BPLName") or ""
                        doc_type = str(s_item.get("ObjectCode")).strip()
                        
                        if s_code is not None:
                            code_str = str(s_code).strip()
                            bpl_id_str = str(bpl_id) if bpl_id is not None else "1"
                            bpl_name_str = str(bpl_name).strip() if bpl_name else branches_map.get(bpl_id_str, "Branch " + bpl_id_str)

                            extra_obj = {
                                "doc_type": doc_type,
                                "bpl_id": bpl_id_str,
                                "bpl_name": bpl_name_str,
                                "indicator": indicator or "26-27",
                                "locked": "tNO"
                            }
                            master_records.append({
                                "category": "series",
                                "code": code_str,
                                "name": str(s_name or code_str).strip(),
                                "extra_data": json.dumps(extra_obj),
                            })
                            
                    next_link = data.get("odata.nextLink")
                    if next_link:
                        # Sometimes nextLink is absolute, sometimes relative.
                        if next_link.startswith("http"):
                            list_url = next_link
                        else:
                            # construct absolute url if necessary, but SL usually gives relative like /b1s/v1/... or absolute
                            # assuming it's relative like SQLQueries('ocr_get_ap_series')/List?$skip=20
                            if next_link.startswith("/b1s/v1/"):
                                list_url = base.replace("/b1s/v1", "") + next_link
                            else:
                                list_url = f"{base}/{next_link}"
                    else:
                        list_url = None
                        
                log.info("SAP Master Sync: Fetched %d document series from SQL query", total_series_count)
            except Exception as exc:
                log.warning("SAP master sync Series warning: %s", exc)

        # 8. Service Accounting Codes (SAC Entries from IndiaSacCode)
        if not target or target == "sac_entries":
            try:
                sac_url = f"{base}/IndiaSacCode"
                sac_list = _fetch_all_odata_pages(session, sac_url)
                for d in sac_list:
                    s_code = d.get("ServiceCode")
                    s_name = d.get("ServiceName")
                    
                    if s_code is not None:
                        code_str = str(s_code).strip()
                        master_records.append({
                            "category": "sac_entries",
                            "code": code_str,
                            "name": str(s_name or code_str).strip(),
                            "extra_data": str(d.get("AbsEntry", "")),
                        })
                log.info("SAP Master Sync: Fetched %d SAC entries from IndiaSacCode", len(sac_list))
            except Exception as exc:
                log.warning("SAP master sync OSAC warning: %s", exc)

        # 9. Place of Supply (States)
        if not target or target == "place_of_supply":
            try:
                states_url = f"{base}/States?$select=Code,Name,Country"
                states_list = _fetch_all_odata_pages(session, states_url)
                count = 0
                for s in states_list:
                    code_str = str(s.get("Code")).strip()
                    # Filter for 'IN' in Python, or just get all. Let's just get IN to avoid clutter, 
                    # but if we get 0, let's get all. Actually let's get all states.
                    if code_str and code_str != "None":
                        master_records.append({
                            "category": "place_of_supply",
                            "code": code_str,
                            "name": str(s.get("Name") or code_str).strip(),
                            "extra_data": str(s.get("Country") or "IN"),
                        })
                        count += 1
                log.info("SAP Master Sync: Fetched %d Place of Supply states", count)
            except Exception as exc:
                log.warning("SAP master sync Place of Supply warning: %s", exc)

        # 10. Locations (WarehouseLocations)
        if not target or target == "locations":
            try:
                loc_url = f"{base}/WarehouseLocations?$select=Code,Name"
                loc_list = _fetch_all_odata_pages(session, loc_url)
                count = 0
                for l in loc_list:
                    code_str = str(l.get("Code")).strip()
                    if code_str and code_str != "None":
                        master_records.append({
                            "category": "locations",
                            "code": code_str,
                            "name": str(l.get("Name") or code_str).strip(),
                            "extra_data": "",
                        })
                        count += 1
                log.info("SAP Master Sync: Fetched %d Locations", count)
            except Exception as exc:
                log.warning("SAP master sync Locations warning: %s", exc)

        return master_records
    finally:
        session.close()

def fetch_historical_ap_invoices(top: int = 100) -> list[dict]:
    """Fetch historical posted AP Invoices (PurchaseInvoices) from SAP B1 Service Layer."""
    session, sid = login_and_get_session()
    try:
        base = settings.base_url.rstrip('/')
        url = f"{base}/PurchaseInvoices?$select=DocEntry,DocNum,CardCode,CardName,DocDate,Series,BPL_IDAssignedToInvoice,DocumentLines&$top={top}&$orderby=DocEntry desc"
        res = session.get(url, verify=False, timeout=30)
        if res.ok:
            return res.json().get("value", [])
        log.warning("SAP PurchaseInvoices GET failed (%d): %s", res.status_code, res.text[:200])
        return []
    except Exception as exc:
        log.warning("Failed to fetch historical AP Invoices from SAP: %s", exc)
        return []
    finally:
        logout(session)
