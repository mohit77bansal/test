"""AWS Textract adapter (ap-south-1): AnalyzeExpense for invoices, AnalyzeDocument queries for KYC.

The optional LLM step (LLM_ENGINE=bedrock) normalises the OCR output into the fixed schema and
fills the GST split / HSN; without it a rule-based mapping is used.
"""
from decimal import Decimal, InvalidOperation

import boto3
from django.conf import settings

from apps.parsing import llm

QUERIES = {
    "PAN": [("pan", "What is the PAN number?"), ("name", "What is the name on the card?")],
    "GST": [("gstin", "What is the GSTIN / registration number?"), ("legal_name", "What is the legal name?"), ("trade_name", "What is the trade name?"), ("address", "What is the principal place of business?")],
    "CHEQUE": [("account_no", "What is the account number?"), ("ifsc", "What is the IFSC code?"), ("holder_name", "What is the account holder name?"), ("bank_name", "What is the bank name?")],
    "UDYAM": [("udyam_no", "What is the Udyam registration number?"), ("name", "What is the name of the enterprise?"), ("msme_type", "What is the type of enterprise?")],
}

EXPENSE_MAP = {
    "INVOICE_RECEIPT_ID": "invoice_no",
    "INVOICE_RECEIPT_DATE": "invoice_date",
    "PO_NUMBER": "po_no",
    "SUBTOTAL": "taxable",
    "TOTAL": "total",
    "TAX_PAYER_ID": "supplier_gstin",
    "VENDOR_GST_NUMBER": "supplier_gstin",
}


def _money(text):
    try:
        return str(Decimal("".join(ch for ch in (text or "") if ch.isdigit() or ch == ".")))
    except InvalidOperation:
        return None


class TextractEngine:
    name = "textract"

    def __init__(self):
        self.client = boto3.client("textract", region_name=settings.AWS_S3_REGION_NAME)

    def parse(self, kind, content: bytes, *, vendor=None, sha256=""):
        if kind == "INVOICE":
            raw = self.client.analyze_expense(Document={"Bytes": content})
            extracted, confidence = self._map_expense(raw)
        else:
            raw = self.client.analyze_document(
                Document={"Bytes": content},
                FeatureTypes=["QUERIES"],
                QueriesConfig={"Queries": [{"Text": q, "Alias": alias} for alias, q in QUERIES[kind]]},
            )
            extracted, confidence = self._map_queries(raw)
        if settings.LLM_ENGINE != "none":
            extracted = llm.normalise(kind, raw, extracted)
        return raw, extracted, confidence

    def _map_expense(self, raw):
        extracted, confidence, tax = {"line_items": []}, {}, Decimal("0")
        for doc in raw.get("ExpenseDocuments", []):
            for f in doc.get("SummaryFields", []):
                ftype = f.get("Type", {}).get("Text")
                value = f.get("ValueDetection", {}).get("Text", "")
                conf = f.get("ValueDetection", {}).get("Confidence", 0) / 100
                if ftype == "TAX":
                    tax += Decimal(_money(value) or 0)
                    confidence["cgst"] = confidence["sgst"] = min(conf, 0.7)
                field = EXPENSE_MAP.get(ftype)
                if field and field not in extracted:
                    extracted[field] = _money(value) if field in ("taxable", "total") else value.strip()
                    confidence[field] = conf
            for group in doc.get("LineItemGroups", []):
                for item in group.get("LineItems", []):
                    li = {}
                    for f in item.get("LineItemExpenseFields", []):
                        t = f.get("Type", {}).get("Text")
                        v = f.get("ValueDetection", {}).get("Text", "")
                        if t == "ITEM":
                            li["description"] = v
                        elif t == "QUANTITY":
                            li["qty"] = _money(v)
                        elif t == "UNIT_PRICE":
                            li["rate"] = _money(v)
                        elif t == "PRICE":
                            li["amount"] = _money(v)
                    extracted["line_items"].append(li)
        # Without the LLM step the GST split is a low-confidence guess (intra-state assumed).
        half = (tax / 2).quantize(Decimal("0.01"))
        extracted.setdefault("cgst", str(half))
        extracted.setdefault("sgst", str(tax - half))
        extracted.setdefault("igst", "0")
        return extracted, confidence

    def _map_queries(self, raw):
        extracted, confidence = {}, {}
        blocks = {b["Id"]: b for b in raw.get("Blocks", [])}
        for b in raw.get("Blocks", []):
            if b.get("BlockType") != "QUERY":
                continue
            alias = b.get("Query", {}).get("Alias")
            for rel in b.get("Relationships", []):
                for rid in rel.get("Ids", []):
                    ans = blocks.get(rid, {})
                    if ans.get("BlockType") == "QUERY_RESULT":
                        extracted[alias] = ans.get("Text", "").strip()
                        confidence[alias] = ans.get("Confidence", 0) / 100
        return extracted, confidence
