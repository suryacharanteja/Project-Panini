"""Bounded plain-text delivery preserves model text without interpreting it as provider markup."""


def chunks(text, limit=3500):
    if not 1 <= limit <= 4096:
        raise ValueError("Invalid channel chunk limit.")
    text = str(text or "").replace("\x00", "")
    # Telegram counts UTF-16 code units; astral characters consume two.
    result, current, units = [], [], 0
    for character in text:
        size = 2 if ord(character) > 0xFFFF else 1
        if units + size > limit:
            if not current:
                raise ValueError("Chunk limit cannot hold this character.")
            result.append("".join(current))
            current, units = [], 0
        current.append(character)
        units += size
    if current:
        result.append("".join(current))
    return result or ["PANINI has no text result for this request."]
