"""Optional AI helper for BMO: "what screen is this?" when the macro is stuck.

OFF BY DEFAULT. Nothing is sent anywhere unless you turn it on.

What it does: when the macro has been stuck for a couple of minutes, it can send ONE small picture of the
Roblox window to a Claude vision model and get back a label (lobby, disconnected, loading, pop-up, ...) plus a
short reason. BMO writes that into the log. It never clicks or presses anything based on the answer.

How to turn it on: create `ai_assist.json` next to macro.py:

    {"enabled": true, "api_key": "sk-ant-...", "model": "claude-haiku-5-5"}

(or set the BMO_AI_KEY environment variable instead of putting the key in the file).
Never share ai_assist.json. It uses your own Anthropic API key and costs a fraction of a cent per call.
Calls are limited: at most `max_calls` per session and `min_gap_minutes` apart.

Only the Roblox window picture is sent (max 960 px wide). It may show your username or chat.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "ai_assist.json"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"

LABELS = {
    "in_match": "a tower-defense match is being played (map, units, wave counter)",
    "hub_lobby": "the game's hub or lobby, ready to start something",
    "roblox_home": "the Roblox website/app home screen, not inside a game",
    "game_menu": "a Roblox menu, Favorites list or game details page",
    "reconnect_prompt": "a Roblox disconnected / reconnect / error message",
    "loading": "a loading screen or black screen while a game starts",
    "popup_dialog": "a pop-up or dialog box covering the game",
    "other": "none of the above",
}

_state = {"calls": 0, "last": 0.0}


def _config():
    cfg = {"enabled": False, "api_key": "", "model": DEFAULT_MODEL, "max_calls": 6, "min_gap_minutes": 10}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in cfg})
    except Exception:
        pass
    cfg["api_key"] = (os.environ.get("BMO_AI_KEY") or cfg["api_key"] or "").strip()
    return cfg


def available():
    """True only if the user switched this on AND gave a key."""
    cfg = _config()
    return bool(cfg["enabled"] and cfg["api_key"])


def _prompt():
    lines = "\n".join(f'- "{k}": {v}' for k, v in LABELS.items())
    return ("This is a screenshot from a Roblox tower-defense game that an automation script is stuck on. "
            "Pick the ONE label that best describes what is on screen:\n" + lines +
            '\n\nAnswer with JSON only, like {"label": "loading", "reason": "short sentence"}. '
            "Do not give instructions or coordinates.")


def diagnose(jpeg_bytes, timeout=30):
    """Return {"label": str, "reason": str} or None (disabled, rate-limited, or any error)."""
    cfg = _config()
    if not (cfg["enabled"] and cfg["api_key"]):
        return None
    now = time.time()
    if _state["calls"] >= int(cfg["max_calls"]) or now - _state["last"] < float(cfg["min_gap_minutes"]) * 60:
        return None
    _state["calls"] += 1
    _state["last"] = now
    body = {
        "model": cfg["model"] or DEFAULT_MODEL,
        "max_tokens": 150,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg_bytes).decode("ascii")}},
            {"type": "text", "text": _prompt()},
        ]}],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-api-key": cfg["api_key"], "anthropic-version": API_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")
        start, end = text.find("{"), text.rfind("}")
        out = json.loads(text[start:end + 1])
        label = str(out.get("label", "other"))
        if label not in LABELS:
            label = "other"
        return {"label": label, "reason": str(out.get("reason", ""))[:200]}
    except urllib.error.HTTPError as e:
        return {"label": "error", "reason": f"AI helper request failed (HTTP {e.code}); check your key and model"}
    except Exception as e:
        return {"label": "error", "reason": f"AI helper request failed ({type(e).__name__})"}
