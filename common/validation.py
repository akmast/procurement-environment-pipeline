"""
Content validation — confirms a downloaded file is actually readable in
its expected format before it's allowed to reach final storage. This is
a structural check (can the format's own reader parse these bytes), not
a content/schema check — that's normalization's job, not ingestion's.

    from common.validation import is_valid_json, is_valid_parquet, is_valid_xml
"""
import json
import logging
import xml.etree.ElementTree as ET
from io import BytesIO

logger = logging.getLogger(__name__)


def is_valid_json(content: bytes) -> bool:
    try:
        json.loads(content)
        return True
    except (ValueError, UnicodeDecodeError) as exc:
        logger.error("JSON validation failed | error=%s", exc)
        return False


def is_valid_parquet(content: bytes) -> bool:
    import pandas as pd
    try:
        pd.read_parquet(BytesIO(content))
        return True
    except Exception as exc:  # pyarrow raises its own exception hierarchy
        logger.error("Parquet validation failed | error=%s", exc)
        return False


def is_valid_xml(content: bytes) -> bool:
    try:
        ET.fromstring(content)
        return True
    except ET.ParseError as exc:
        logger.error("XML validation failed | error=%s", exc)
        return False
