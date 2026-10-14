"""Application-level encryption for sensitive fields (bank account number, PAN).

Local/test: FIELD_ENCRYPTION_KEY (Fernet). UAT/prod: KMS_ENCRYPTED_DATA_KEY is a KMS-encrypted
Fernet data key, decrypted once at start-up with KMS_KEY_ID.
"""
import base64
import functools
import os

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@functools.cache
def _fernet():
    encrypted = os.environ.get("KMS_ENCRYPTED_DATA_KEY")
    if encrypted:
        import boto3

        kms = boto3.client("kms", region_name=settings.AWS_S3_REGION_NAME)
        plaintext = kms.decrypt(CiphertextBlob=base64.b64decode(encrypted), KeyId=settings.KMS_KEY_ID)["Plaintext"]
        return Fernet(base64.urlsafe_b64encode(plaintext[:32]))
    if not settings.FIELD_ENCRYPTION_KEY:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY or KMS_ENCRYPTED_DATA_KEY must be set")
    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode())


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()


def mask(last4: str) -> str:
    return f"XXXX{last4}" if last4 else ""
