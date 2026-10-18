"""Telegram as a messenger channel: the assistant's messages delivered to the owner's Telegram chat.

Setup: create a bot with @BotFather, paste its token, press Start in the bot; we find the chat via getUpdates.
Stdlib only (Bot API over HTTPS). Sending runs in a background thread so the app never waits on the network.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

API = "https://api.telegram.org/bot{token}/{method}"
DELIVERY = {  # what gets forwarded, by message kind
    "important": {"brief", "recovery", "recap", "alert"},
    "all": {"brief", "recovery", "recap", "alert", "nudge", "last_call"},
    "off": set(),
}


class TelegramError(RuntimeError):
    """Safe to show to the owner."""


@dataclass
class TelegramChannel:
    token: str = ""
    bot: str = ""        # @username of the owner's bot
    chat_id: int | None = None
    chat_name: str = ""
    delivery: str = "important"
    last_error: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.token and self.chat_id)

    def public(self) -> dict:
        """State for the UI — never includes the token."""
        return {"connected": bool(self.token), "ready": self.ready, "bot": self.bot, "chat_name": self.chat_name,
                "delivery": self.delivery, "last_error": self.last_error}

    def saved(self) -> dict:
        return asdict(self)

    # --- setup ----------------------------------------------------------------------------

    def connect(self, token: str) -> None:
        me = _call(token.strip(), "getMe")
        self.token, self.bot, self.chat_id, self.chat_name, self.last_error = token.strip(), me["username"], None, "", ""

    def find_chat(self) -> None:
        """The chat of the latest message sent to the bot (the owner pressed Start)."""
        updates = _call(self.token, "getUpdates")
        chats = [u["message"]["chat"] for u in updates if "message" in u]
        if not chats:
            raise TelegramError(f"No message found — open @{self.bot} in Telegram, press Start, then try again.")
        chat = chats[-1]
        self.chat_id, self.chat_name = chat["id"], chat.get("first_name") or chat.get("title") or str(chat["id"])

    def set_delivery(self, delivery: str) -> None:
        if delivery not in DELIVERY:
            raise ValueError(f"unknown delivery '{delivery}'")
        self.delivery = delivery

    # --- sending --------------------------------------------------------------------------

    def send_now(self, text: str) -> None:
        if not self.ready:
            raise TelegramError("Connect a bot and find your chat first.")
        _call(self.token, "sendMessage", {"chat_id": self.chat_id, "text": text})

    def forward(self, kind: str, text: str) -> None:
        """Deliver in the background if this kind of message is wanted; errors are kept for the UI."""
        if self.ready and kind in DELIVERY[self.delivery]:
            threading.Thread(target=self._send_quietly, args=(text,), daemon=True).start()

    def _send_quietly(self, text: str) -> None:
        try:
            self.send_now(text)
            self.last_error = ""
        except TelegramError as e:
            self.last_error = str(e)


def _call(token: str, method: str, params: dict | None = None):
    if not token:
        raise TelegramError("Paste your bot token from @BotFather first.")
    request = urllib.request.Request(API.format(token=token, method=method), data=json.dumps(params or {}).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as e:
        payload = _error_payload(e)
    except (urllib.error.URLError, TimeoutError):
        raise TelegramError("Can't reach Telegram — check the internet connection.") from None
    if not payload.get("ok"):
        raise TelegramError(_explain(payload))
    return payload["result"]


def _error_payload(e: urllib.error.HTTPError) -> dict:
    try:
        return json.load(e)
    except ValueError:
        return {"ok": False, "error_code": e.code, "description": e.reason}


def _explain(payload: dict) -> str:
    if payload.get("error_code") in (401, 404):
        return "Telegram didn't accept this token — copy it again from @BotFather."
    return f"Telegram: {payload.get('description', 'unknown error')}"
