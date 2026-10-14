"""Fixed JSON schema per document type (Pydantic-validated) — the parser's output contract."""
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str = ""
    hsn_sac: str = ""
    qty: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None


class InvoiceExtract(BaseModel):
    supplier_gstin: str = ""
    invoice_no: str = ""
    invoice_date: Optional[date] = None
    po_no: str = ""
    line_items: list[LineItem] = Field(default_factory=list)
    taxable: Optional[Decimal] = None
    cgst: Optional[Decimal] = None
    sgst: Optional[Decimal] = None
    igst: Optional[Decimal] = None
    total: Optional[Decimal] = None
    irn: str = ""


class PanExtract(BaseModel):
    pan: str = ""
    name: str = ""


class GstExtract(BaseModel):
    gstin: str = ""
    legal_name: str = ""
    trade_name: str = ""
    address: str = ""


class ChequeExtract(BaseModel):
    account_no: str = ""
    ifsc: str = ""
    holder_name: str = ""
    bank_name: str = ""


class UdyamExtract(BaseModel):
    udyam_no: str = ""
    name: str = ""
    msme_type: str = ""


SCHEMAS = {"INVOICE": InvoiceExtract, "PAN": PanExtract, "GST": GstExtract, "CHEQUE": ChequeExtract, "UDYAM": UdyamExtract}
