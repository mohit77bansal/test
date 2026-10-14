"""Fixed JSON schema per document type (Pydantic-validated) — the parser's output contract."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str = ""
    hsn_sac: str = ""
    qty: Decimal | None = None
    rate: Decimal | None = None
    amount: Decimal | None = None
    tax_rate: Decimal | None = None


class InvoiceExtract(BaseModel):
    supplier_gstin: str = ""
    invoice_no: str = ""
    invoice_date: date | None = None
    po_no: str = ""
    line_items: list[LineItem] = Field(default_factory=list)
    taxable: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    total: Decimal | None = None
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
