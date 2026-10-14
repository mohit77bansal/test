"""Parser engines. Each returns (raw_json, extracted_dict, confidence_dict)."""
from django.conf import settings


def get_engine(name=None):
    name = name or settings.PARSER_ENGINE
    if name == "fake":
        from .fake import FakeEngine

        return FakeEngine()
    if name == "textract":
        from .textract import TextractEngine

        return TextractEngine()
    raise ValueError(f"Unknown PARSER_ENGINE {name!r}")
