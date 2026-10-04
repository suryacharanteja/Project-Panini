"""Provider IDs, never display names, authorize an owner conversation."""

from ..core_contracts import Principal


def owner(config, message):
    if not message.human or message.kind not in {"dm", "mention", "reply"}:
        return None
    if message.channel != "telegram":
        return None  # Other providers have separate identity and ingress requirements.
    if message.sender_id not in config.get("owner_ids", []):
        return None
    chat = message.thread_key.split(":", 1)[0]
    if message.chat_type == "private":
        if chat != message.sender_id:
            return None
    elif message.chat_type not in {"group", "supergroup"} or chat not in config.get("group_ids", []):
        return None
    return Principal(kind="owner", surface="telegram", verified=True)
