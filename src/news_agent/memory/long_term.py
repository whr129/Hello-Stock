def should_store_memory(text: str) -> bool:
    lowered = text.lower()
    markers = ("remember", "i prefer", "my local", "my region", "don't show", "block")
    return any(marker in lowered for marker in markers)


def memory_from_user_text(text: str) -> str:
    return text.strip()
