"""Email for a dedicated mailbox (V2-17 §5.5): IMAP polling and SMTP replies, standard library only.

- A message counts as from the owner only when its From address is allow-listed **and** the receiving server's
  Authentication-Results shows dkim=pass or spf=pass for a domain aligned with the From domain.
- Never answers mailing lists (List-Id), auto-generated mail (Auto-Submitted, Precedence bulk/list/junk) or
  PANINI's own address. Threads follow the References/In-Reply-To root; replies quote no history.
- The last processed UID is stored only after delivery, so a restart never skips or repeats a message.
"""

import asyncio
import email
import email.policy
import imaplib
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from ..core_contracts import Readiness
from .base import InboundMessage, safe_failure

POLL_S = 30
RESULT = re.compile(
    r"\b(dkim|spf)=pass\b[^;]*?(?:header\.d|header\.i|smtp\.mailfrom)=@?([A-Za-z0-9.-]+)", re.I
)


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].lower()


def aligned(sender_domain: str, authenticated_domain: str) -> bool:
    a, b = sender_domain.lower().strip("."), authenticated_domain.lower().strip(".")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def authenticated(message) -> bool:
    """dkim=pass or spf=pass for a domain aligned with the From domain, per the receiving server."""
    sender = domain_of(parseaddr(str(message.get("From", "")))[1])
    if not sender:
        return False
    for header in message.get_all("Authentication-Results", []) or []:
        for _, domain in RESULT.findall(str(header)):
            if aligned(sender, domain):
                return True
    return False


def automated(message) -> bool:
    if message.get("List-Id") or message.get("List-Unsubscribe"):
        return True
    if str(message.get("Auto-Submitted", "no")).lower() != "no":
        return True
    return str(message.get("Precedence", "")).lower() in {"bulk", "list", "junk"}


def root_of(message) -> str:
    references = str(message.get("References", "")).split()
    return (references[0] if references else str(message.get("In-Reply-To", "")).strip()) or str(
        message.get("Message-ID", "")
    ).strip()


def body_text(message) -> str:
    part = message.get_body(preferencelist=("plain",)) if hasattr(message, "get_body") else None
    text = part.get_content() if part is not None else ""
    lines = []
    for line in str(text).splitlines():
        if line.startswith(">") or re.match(r"^On .+ wrote:$", line.strip()):
            break  # Quoted history is not part of the request.
        lines.append(line)
    return "\n".join(lines).strip()[:4000]


class Email:
    name = "email"

    def __init__(self, account, accounts, *, imap=None, smtp=None):
        self.account, self.accounts = account, accounts
        config = account["config"]
        self.imap = imap or (lambda: imaplib.IMAP4_SSL(config["imap_host"], int(config.get("imap_port") or 993),
                                                        ssl_context=ssl.create_default_context()))  # fmt: skip
        port = int(config.get("smtp_port") or 587)
        self.smtp = smtp or (
            (lambda: smtplib.SMTP_SSL(config["smtp_host"], port, context=ssl.create_default_context()))
            if port == 465
            else (lambda: smtplib.SMTP(config["smtp_host"], port, timeout=30))
        )
        self.paused = lambda: False
        self.task = None
        self.state = "off"

    # Receiving ------------------------------------------------------------------------------------
    def fetch_new(self):
        """(uid, parsed message) for messages after the last processed UID. Runs in a thread."""
        config, identifier = self.account["config"], self.account["id"]
        last = int(self.accounts.state(identifier, "last_uid", 0))
        box = self.imap()
        try:
            box.login(config["username"], self.accounts.secret(identifier))
            box.select("INBOX", readonly=True)
            status, data = box.uid("SEARCH", None, f"UID {last + 1}:*")
            if status != "OK":
                raise ConnectionError("IMAP search failed.")
            found = []
            for uid in sorted(int(u) for u in (data[0] or b"").split() if int(u) > last):
                status, parts = box.uid("FETCH", str(uid), "(RFC822)")
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    found.append((uid, email.message_from_bytes(parts[0][1], policy=email.policy.default)))
            return found
        finally:
            try:
                box.logout()
            except Exception:  # noqa: BLE001
                pass

    def normalize(self, message):
        own = self.account["config"]["address"].lower()
        sender = parseaddr(str(message.get("From", "")))[1].lower()
        if not sender or sender == own or automated(message):
            return None
        message_id = str(message.get("Message-ID", "")).strip()
        if not message_id:
            return None
        root = root_of(message)
        # Remember how to answer this thread (the newest message we were sent in it).
        self.accounts.set_state(self.account["id"], f"thread:{root}", {
            "to": sender, "subject": str(message.get("Subject", ""))[:200], "last": message_id,
            "references": (str(message.get("References", "")) + " " + message_id).strip()[-2000:],
        })  # fmt: skip
        return InboundMessage("email", self.account["id"], root, message_id, sender, body_text(message),
                              kind="dm", human=True,
                              chat_type="authenticated" if authenticated(message) else "unauthenticated")  # fmt: skip

    async def poll_once(self, deliver):
        for uid, message in await asyncio.to_thread(self.fetch_new):
            inbound = self.normalize(message)
            if inbound is not None:
                await deliver(inbound)
            self.accounts.set_state(self.account["id"], "last_uid", uid)  # Only after durable delivery.

    async def run(self, deliver):
        backoff = POLL_S
        while True:
            try:
                await self.poll_once(deliver)
                self.state, backoff = "ready", POLL_S
                await asyncio.sleep(POLL_S)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 -- provider failures are isolated and redacted.
                self.state = "degraded"
                safe_failure(error)
                await asyncio.sleep(backoff)
                backoff = min(600, backoff * 2)

    async def start(self, deliver):
        if self.task:
            return
        self.state = "connecting"
        self.task = asyncio.create_task(self.run(deliver))

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.state = "off"

    # Sending --------------------------------------------------------------------------------------
    def compose(self, thread_key, text):
        thread = self.accounts.state(self.account["id"], f"thread:{thread_key}", None)
        if not thread:
            raise ValueError("No email thread to reply to.")
        subject = thread["subject"] or "PANINI"
        reply = EmailMessage()
        reply["From"] = self.account["config"]["address"]
        reply["To"] = thread["to"]
        reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        reply["In-Reply-To"] = thread["last"]
        reply["References"] = thread["references"]
        reply["Auto-Submitted"] = "auto-replied"  # Other systems must not answer PANINI automatically.
        reply["Message-ID"] = make_msgid(domain=domain_of(self.account["config"]["address"]) or None)
        reply.set_content(text)
        return reply

    def deliver_now(self, reply):
        config = self.account["config"]
        server = self.smtp()
        try:
            if isinstance(server, smtplib.SMTP) and not isinstance(server, smtplib.SMTP_SSL):
                server.starttls(context=ssl.create_default_context())
            server.login(config["username"], self.accounts.secret(self.account["id"]))
            server.send_message(reply)
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass

    async def send(self, thread_key, message):
        reply = self.compose(thread_key, message.text)
        await asyncio.to_thread(self.deliver_now, reply)
        return str(reply["Message-ID"])

    async def typing(self, thread_key):
        return None

    async def readiness(self):
        return Readiness(self.state if self.state in {"ready", "degraded"} else "needs_setup", self.state)
