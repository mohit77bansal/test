"""LLM normalisation step (LLM_ENGINE=bedrock keeps documents inside AWS ap-south-1)."""
import json

from django.conf import settings

from .schemas import SCHEMAS


def normalise(kind, raw, extracted):
    if settings.LLM_ENGINE != "bedrock":
        return extracted
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.AWS_S3_REGION_NAME)
    schema = SCHEMAS[kind].model_json_schema()
    prompt = (
        "You normalise OCR output from an Indian business document into JSON matching this schema. "
        "Fill the CGST/SGST/IGST split and HSN/SAC when they can be read. Use null when unknown. "
        f"Reply with JSON only.\nSchema: {json.dumps(schema)}\nCurrent mapping: {json.dumps(extracted, default=str)}\n"
        f"OCR: {json.dumps(raw, default=str)[:60000]}"
    )
    resp = client.converse(
        modelId=settings.BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 4000, "temperature": 0},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    return {**extracted, **{k: v for k, v in data.items() if v not in (None, "")}}
