"""
Telegram notification module for AlphaTrade.
Non-blocking: every send runs on a short-lived daemon thread.
"""
import threading
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/London")

# ── Global config (safe to read/write from multiple threads) ──────────────────
_config: dict = {
    "enabled": False,
    "token":   "",
    "chat_id": "",
}
_lock = threading.Lock()


# ── Public API ────────────────────────────────────────────────────────────────

def configure(token: str, chat_id: str, enabled: bool = True) -> None:
    """Update credentials and enabled state. Call from dashboard on save."""
    with _lock:
        _config["token"]   = token.strip()
        _config["chat_id"] = chat_id.strip()
        _config["enabled"] = enabled and bool(token.strip()) and bool(chat_id.strip())


def is_enabled() -> bool:
    with _lock:
        return _config["enabled"]


def trade_open(
    symbol:   str,
    side:     str,
    price:    float,
    invested: float,
    reason:   str,
    mode:     str = "",
) -> None:
    icon = "🚀" if side == "BUY" else "🔻"
    mode_tag = f"  <i>[{mode}]</i>" if mode else ""
    _send(
        f"{icon} <b>{side} {symbol}</b>{mode_tag}\n"
        f"💰 Price:  <code>${price:,.4f}</code>\n"
        f"💵 Amount: <code>${invested:.2f} USDT</code>\n"
        f"📊 Reason: {reason}\n"
        f"🕐 Time:   {_now()} (LON)"
    )


def trade_close(
    symbol:     str,
    side:       str,
    entry:      float,
    exit_price: float,
    pnl:        float,
    pct:        float,
    reason:     str,
    mode:       str = "",
) -> None:
    icon = "🟢" if pnl >= 0 else "🔴"
    sign = "+" if pnl >= 0 else ""
    mode_tag = f"  <i>[{mode}]</i>" if mode else ""
    _send(
        f"{icon} <b>CLOSE {side} {symbol}</b>{mode_tag}\n"
        f"📈 Entry → Exit: <code>${entry:,.4f}</code> → <code>${exit_price:,.4f}</code>\n"
        f"💹 P&amp;L: <code>{sign}${pnl:.4f}  ({sign}{pct:.2f}%)</code>\n"
        f"📌 Reason: {reason}\n"
        f"🕐 Time:   {_now()} (LON)"
    )


def bot_event(event: str, detail: str = "") -> None:
    """Send a bot lifecycle alert (started / stopped / emergency)."""
    icons = {"started": "▶️", "stopped": "⏹️", "emergency": "🚨"}
    icon  = icons.get(event.lower(), "ℹ️")
    body  = f"{icon} <b>Bot {event.upper()}</b>"
    if detail:
        body += f"\n{detail}"
    body += f"\n🕐 {_now()} (LON)"
    _send(body)


def error_alert(message: str) -> None:
    _send(f"⚠️ <b>AlphaTrade ERROR</b>\n{message}\n🕐 {_now()} (LON)")


def test_notification() -> tuple[bool, str]:
    """Blocking test send — returns (ok, message) for UI feedback."""
    with _lock:
        token   = _config["token"]
        chat_id = _config["chat_id"]
    if not token or not chat_id:
        return False, "Token or Chat ID is empty"
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            data={
                "chat_id":    chat_id,
                "text":       (
                    f"✅ <b>AlphaTrade — test notification</b>\n"
                    f"Notifications are working correctly.\n"
                    f"🕐 {_now()} (LON)"
                ),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "Message delivered ✅"
        data = resp.json()
        return False, data.get("description", f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        return False, "Request timed out — check your network"
    except Exception as exc:
        return False, str(exc)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(_TZ).strftime("%H:%M:%S")


def _send(text: str) -> None:
    """Fire-and-forget in a daemon thread."""
    with _lock:
        token   = _config["token"]
        chat_id = _config["chat_id"]
        enabled = _config["enabled"]
    if not enabled or not token or not chat_id:
        return

    def _do() -> None:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception:
            pass

    threading.Thread(target=_do, daemon=True, name="tg-send").start()
