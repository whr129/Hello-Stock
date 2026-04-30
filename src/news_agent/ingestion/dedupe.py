import hashlib
import re


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def content_hash(*parts: str | None) -> str:
    normalized = "|".join(normalize_title(part or "") for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
