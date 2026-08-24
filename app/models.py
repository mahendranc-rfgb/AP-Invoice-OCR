from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2, ge=0)]
Percentage = Annotated[Decimal, Field(max_digits=5, decimal_places=2, ge=0, le=100)]


class DocumentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    MAPPED = "MAPPED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    SAP_DRAFT_READY = "SAP_DRAFT_READY"
    POSTED = "POSTED"
    ERROR = "ERROR"


class InvoiceHeader(BaseModel):
    supplier_name: str = Field(min_length=1)
    supplier_gstin: str | None = None
    invoice_number: str = Field(min_length=1)
    vendor_ref_no: str | None = None
    contact_person: str | None = None
    invoice_date: date
    document_date: date | None = None
    posting_date: date | None = None
    due_date: date | None = None
    local_currency: str = Field(default="INR", min_length=3, max_length=3)
    series: int | None = None
    bpl_id_assigned_to_invoice: int = Field(default=1, ge=1)
    payment_group_code: int = Field(default=10, ge=1)
    po_number: str | None = None
    transaction_type: str | None = None
    place_of_supply: str | None = None
    branch: str | None = None
    vat_reg_num: str | None = None
    comments: str | None = None
    summary_type: str | None = None
    wt_code: str | None = None

    # Accounting Tab Fields (Matching SAP B1 Accounting Tab)
    journal_memo: str | None = None
    control_account: str | None = None
    payment_block: str | None = None
    payment_method: str | None = None
    central_bank_indicator: str | None = None
    number_of_installments: int | None = 1

    # Logistics Tab Fields (Matching SAP B1 Logistics Tab)
    ship_to_code: str | None = None
    pay_to_code: str | None = None
    ship_to_address: str | None = None
    pay_to_address: str | None = None
    transportation_code: int | None = None

    @field_validator("local_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class SupplierMapping(BaseModel):
    sap_card_code: str | None = None
    confidence: float = Field(default=0, ge=0, le=100)
    reason: str | None = None


class InvoiceLine(BaseModel):
    line_number: int = Field(ge=1)
    description: str = Field(min_length=1)
    item_service_type: str | None = None
    supplier_item_code: str | None = None
    quantity: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
    uom: str | None = None
    unit_price: Money
    discount_percent: Percentage = Decimal("0")
    discount_amount: Money = Decimal("0")
    tax_percentage: Percentage
    tax_amount: Money
    line_total: Money
    sap_item_code: str | None = None
    gl_account: str | None = None
    gl_account_name: str | None = None
    costing_code: str | None = None
    costing_code2: str | None = None
    costing_code3: str | None = "NONE"
    location_code: str | None = None
    department_code: str | None = None
    project_code: str | None = None
    warehouse_code: str | None = None
    tax_code: str | None = None
    tax_type: str | None = None
    tax_liable: str | None = None
    wtax_liable: str | None = "tYES"
    wt_code: str | None = "C004"
    sac_entry: int | None = None
    net_tax_amount: Money | None = None
    gross_price: Money | None = None
    gross_total: Money | None = None
    mapping_confidence: float = Field(default=0, ge=0, le=100)
    mapping_reason: str | None = None
    base_entry: int | None = None
    base_type: int | None = None
    base_line: int | None = None


class InvoiceTotals(BaseModel):
    subtotal: Money
    tax_amount: Money
    wtax_amount: Money | None = Decimal("0")
    grand_total: Money
    discount_percent: Money = Decimal("0.00")
    freight_amount: Money = Decimal("0.00")
    rounding_amount: Money = Decimal("0.00")


class StandardInvoice(BaseModel):
    """SAP-independent canonical model populated by OCR and reviewed by users."""

    invoice_header: InvoiceHeader
    supplier: SupplierMapping = Field(default_factory=SupplierMapping)
    lines: list[InvoiceLine] = Field(min_length=1)
    totals: InvoiceTotals
    field_confidence: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_line_numbers(self) -> "StandardInvoice":
        numbers = [line.line_number for line in self.lines]
        if len(numbers) != len(set(numbers)):
            raise ValueError("line_number values must be unique")
        return self


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: str = "ERROR"
    line_number: int | None = None


class ValidationResult(BaseModel):
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    calculated_subtotal: Decimal
    calculated_tax: Decimal
    calculated_total: Decimal
    overall_confidence: float = Field(ge=0, le=100)
    recommended_status: DocumentStatus


class InvoiceDocument(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    document_id: UUID = Field(default_factory=uuid4)
    source_filename: str | None = None
    invoice: StandardInvoice
    status: DocumentStatus = DocumentStatus.RECEIVED
    validation: ValidationResult | None = None
    approved_by: str | None = None
    sap_doc_num: int | str | None = None
    sap_doc_entry: int | None = None
    sap_response: dict | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalRequest(BaseModel):
    approved_by: str = Field(min_length=1)


class CropBox(BaseModel):
    x: float
    y: float
    width: float
    height: float

class ExtractionTemplate(BaseModel):
    template_id: UUID = Field(default_factory=uuid4)
    card_code: str
    fields_mapping: dict[str, CropBox] = Field(default_factory=dict)
