"""Provider IDs, never display names, authorize an owner conversation (V2-17 §5.3)."""

from ..core_contracts import Principal


def owner(config, message):
    if not message.human or message.kind not in {"dm", "mention", "reply"}:
        return None
    if message.sender_id not in config.get("owner_ids", []):
        return None
    check = CHECKS.get(message.channel)
    if check is None or not check(config, message):
        return None
    return Principal(kind="owner", surface=message.channel, verified=True)


def _telegram(config, message):
    chat = message.thread_key.split(":", 1)[0]
    if message.chat_type == "private":
        return chat == message.sender_id
    return message.chat_type in {"group", "supergroup"} and chat in config.get("group_ids", [])


def _slack(config, message):
    team, channel, _ = (message.thread_key.split(":", 2) + ["", ""])[:3]
    if team != config.get("team_id"):
        return False  # The workspace must match exactly (OpenDots: explicit tenant allow-list).
    if message.chat_type == "im":
        return True
    return channel in config.get("group_ids", [])


def _whatsapp(config, message):
    return message.chat_type == "private" and message.thread_key == message.sender_id


def _email(config, message):
    # The receiving server must have authenticated the sender (aligned DKIM or SPF); see mail.authenticated().
    return message.chat_type == "authenticated"


CHECKS = {"telegram": _telegram, "slack": _slack, "whatsapp": _whatsapp, "email": _email}


def permitted(account, job):
    """Whether a queued job's sender and conversation are still allowed for this account."""
    config = account["config"]
    if job["sender_id"] not in config.get("owner_ids", []):
        return False
    channel = account["channel"]
    if channel == "telegram":
        chat = job["thread_key"].split(":", 1)[0]
        return not chat.startswith("-") or chat in config.get("group_ids", [])
    if channel == "slack":
        team, conversation, _ = (job["thread_key"].split(":", 2) + ["", ""])[:3]
        is_dm = conversation.startswith("D")
        return team == config.get("team_id") and (is_dm or conversation in config.get("group_ids", []))
    return channel in {"whatsapp", "email"}
