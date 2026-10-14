"""File storage, upload validation, and parse_document(kind, file_id)."""
import hashlib
import time
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, HttpResponseRedirect
from pydantic import ValidationError as PydanticError

from apps.core.models import SystemSetting

from . import validators
from .engines import get_engine
from .models import File, ParseJob
from .scan import scan_bytes
from .schemas import SCHEMAS

MAGIC = {b"%PDF": "application/pdf", b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png"}


def sniff_mime(head: bytes):
    for magic, mime in MAGIC.items():
        if head.startswith(magic):
            return mime
    return None


def validate_upload(uploaded):
    if uploaded.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(f"File is larger than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    head = uploaded.read(8)
    uploaded.seek(0)
    mime = sniff_mime(head)
    if mime is None or mime not in settings.ALLOWED_UPLOAD_TYPES:
        raise ValidationError("Only PDF, JPG and PNG files are accepted.")
    return mime


def store_upload(uploaded, *, vendor, user) -> File:
    mime = validate_upload(uploaded)
    content = uploaded.read()
    sha = hashlib.sha256(content).hexdigest()
    ext = settings.ALLOWED_UPLOAD_TYPES[mime]
    key = f"vendors/{vendor.pk if vendor else 'internal'}/{uuid.uuid4().hex}{ext}"
    key = default_storage.save(key, ContentFile(content))
    return File.objects.create(
        vendor=vendor,
        s3_key=key,
        bucket=settings.AWS_STORAGE_BUCKET_NAME if settings.STORAGE_BACKEND == "s3" else "local",
        original_name=uploaded.name[:255],
        mime=mime,
        size_bytes=len(content),
        sha256=sha,
        uploaded_by=user,
    )


def read_file(file: File) -> bytes:
    with default_storage.open(file.s3_key, "rb") as fh:
        return fh.read()


def file_response(file: File):
    """Caller has already checked the user may see the parent record."""
    if settings.STORAGE_BACKEND == "s3":
        return HttpResponseRedirect(default_storage.url(file.s3_key))  # 5-minute pre-signed URL
    resp = FileResponse(default_storage.open(file.s3_key, "rb"), content_type=file.mime)
    resp["Content-Disposition"] = f'inline; filename="{file.original_name}"'
    return resp


def start_parse(file: File, kind: str) -> ParseJob:
    job = ParseJob.objects.create(vendor=file.vendor, file=file, kind=kind, engine=settings.PARSER_ENGINE)
    from .tasks import parse_file_task

    transaction.on_commit(lambda: parse_file_task.delay(str(job.pk)))
    return job


def rule_checks(kind, data, confidence, vendor=None):
    checks = []

    def flag(field, ok, message):
        checks.append({"field": field, "ok": ok, "message": message})
        if not ok:
            confidence[field] = min(float(confidence.get(field, 0.5)), 0.5)

    if kind == "INVOICE":
        if data.get("supplier_gstin"):
            flag("supplier_gstin", validators.valid_gstin(data["supplier_gstin"]), "GSTIN format and checksum")
            if vendor is not None and vendor.pan:
                flag("supplier_gstin", validators.pan_from_gstin(data["supplier_gstin"]) == vendor.pan, "PAN embedded in GSTIN matches vendor PAN")
        if data.get("total") is not None:
            tol = Decimal(str(SystemSetting.get("TOTALS_TOLERANCE")))
            flag("total", validators.totals_reconcile(data.get("taxable"), data.get("cgst"), data.get("sgst"), data.get("igst"), data.get("total"), tol), "Taxable + GST equals total")
        if data.get("invoice_date"):
            from datetime import date

            d = date.fromisoformat(str(data["invoice_date"]))
            flag("invoice_date", d <= date.today(), "Invoice date is not in the future")
    elif kind == "PAN":
        flag("pan", validators.valid_pan(data.get("pan", "")), "PAN format")
    elif kind == "GST":
        g = data.get("gstin", "")
        flag("gstin", validators.valid_gstin(g), "GSTIN format and checksum")
        if vendor is not None and vendor.pan and g:
            flag("gstin", validators.pan_from_gstin(g) == vendor.pan, "PAN embedded in GSTIN")
    elif kind == "CHEQUE":
        flag("ifsc", validators.valid_ifsc(data.get("ifsc", "")), "IFSC format")
    elif kind == "UDYAM":
        flag("udyam_no", bool(validators.UDYAM_RE.match(data.get("udyam_no", ""))), "Udyam number format")
    return checks


def parse_document(kind: str, file_id) -> ParseJob:
    """Run the configured engine on a stored file and save the result to a ParseJob."""
    file = File.objects.select_related("vendor").get(pk=file_id)
    job = ParseJob.objects.filter(file=file, kind=kind).order_by("-created_at").first()
    if job is None:
        job = ParseJob.objects.create(vendor=file.vendor, file=file, kind=kind, engine=settings.PARSER_ENGINE)
    run_job(job)
    return job


def run_job(job: ParseJob):
    file = job.file
    started = time.monotonic()
    job.status = ParseJob.RUNNING
    job.save(update_fields=["status", "updated_at"])
    try:
        content = read_file(file)
        if file.scan_status == File.PENDING:
            file.scan_status = scan_bytes(content)
            file.save(update_fields=["scan_status", "updated_at"])
        if file.scan_status == File.INFECTED:
            raise ValueError("The file failed the virus scan and was not processed.")
        engine = get_engine()
        raw, extracted, confidence = engine.parse(job.kind, content, vendor=file.vendor, sha256=file.sha256)
        model = SCHEMAS[job.kind].model_validate(extracted)
        data = model.model_dump(mode="json")
        job.checks = rule_checks(job.kind, data, confidence, vendor=file.vendor)
        job.engine = engine.name
        job.raw_json = raw
        job.extracted_json = data
        job.confidence = confidence
        job.status = ParseJob.DONE
        job.error = ""
    except (PydanticError, Exception) as exc:  # noqa: BLE001 — any engine failure falls back to manual entry
        job.status = ParseJob.FAILED
        job.error = str(exc)[:2000]
    job.duration_ms = int((time.monotonic() - started) * 1000)
    job.save()
    return job
