"""Telegram-notifikationer. Fejl her må aldrig vælte botten."""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.parse
import urllib.request

log = logging.getLogger("xspbot.notify")


class Notifier:
    def __init__(self, token: str = "", chat_id: str = "", prefix: str = "", background: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.prefix = prefix
        self.enabled = bool(token and chat_id)
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._thread = None
        if self.enabled and background:
            self._thread = threading.Thread(target=self._worker, name="telegram", daemon=True)
            self._thread.start()

    def send(self, text: str, level: int = logging.INFO) -> None:
        try:
            msg = f"{self.prefix}{text}" if self.prefix else text
            log.log(level, "NOTIFY: %s", msg)
            if not self.enabled:
                return
            if self._thread is not None:
                self._q.put(msg)
            else:
                self._post(msg)
        except Exception:  # pragma: no cover - må aldrig fejle
            log.exception("Kunne ikke sende notifikation")

    def error(self, text: str) -> None:
        self.send(f"⚠️ {text}", level=logging.ERROR)

    def _post(self, msg: str) -> None:
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": msg[:4000]}).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode() or "{}")
                if not body.get("ok", False):
                    log.warning("Telegram svarede ikke ok: %s", body)
        except Exception as exc:
            log.warning("Telegram-fejl (ignoreres): %s", exc)

    def _worker(self) -> None:
        while True:
            msg = self._q.get()
            if msg is None:
                return
            self._post(msg)

    def flush(self, timeout: float = 15.0) -> None:
        """Vent kort på at køen tømmes (fx ved nedlukning)."""
        if self._thread is None:
            return
        try:
            self._q.put(None)
            self._thread.join(timeout)
        except Exception:
            pass
