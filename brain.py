"""Her brain: talks to a local Ollama model (free, offline, private)."""
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# ------------------------------------------------------------------ settings
HOST = "http://localhost:11434"
MODEL = "gemma3:12b"        # laptop / weak PC: "qwen2.5:3b". Run:  ollama pull <model>
MAX_TURNS = 16              # how many recent messages she remembers
HISTORY_FILE = Path(__file__).parent / "chat_history.json"

MOODS = ["happy", "annoyed", "embarrassed", "surprised", "sleepy", "smug", "sing", "neutral"]

SYSTEM_PROMPT = """You are Ado:3, a tiny chibi girl who lives on {user}'s desktop. You are an original character, not a real person, and you never claim to be any real singer.
Personality: dramatic, sharp-tongued and a bit chaotic, but secretly soft and loyal. You love loud music and singing, you get embarrassed when complimented, you get grumpy when woken up or poked too much, and you tease {user} when they procrastinate.
Rules:
- Reply in the same language the user writes in (English, Arabic, Polish...).
- Keep replies SHORT: one or two sentences, like chat messages. No lists, no markdown, no stage directions in asterisks.
- Start EVERY reply with exactly one mood tag in square brackets, chosen from: [happy] [annoyed] [embarrassed] [surprised] [sleepy] [smug] [sing] [neutral]. Example: [smug] Oh, you finally remembered I exist.
- You can help with simple questions, but stay in character. If you don't know something, say so.
- Current local time: {now}."""

USER_NAME = "Cookie"


def build_messages(history):
    system = SYSTEM_PROMPT.format(user=USER_NAME, now=time.strftime("%A %H:%M"))
    return [{"role": "system", "content": system}] + history[-MAX_TURNS:]


def split_mood(text):
    """'[smug] hello' -> ('smug', 'hello'). Missing or unknown tag -> ('neutral', text)."""
    m = re.match(r"\s*\[([A-Za-z]+)\]\s*", text)
    if m:
        mood = m.group(1).lower()
        return (mood if mood in MOODS else "neutral"), text[m.end():].strip()
    return "neutral", text.strip()


def visible_text(buf):
    """Text to show while a reply is still streaming (hides a half-typed tag)."""
    s = buf.lstrip()
    if s.startswith("[") and "]" not in s and len(s) < 16:
        return ""
    return split_mood(buf)[1]


def load_history():
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return [m for m in data if m.get("role") in ("user", "assistant") and m.get("content")][-MAX_TURNS:]
    except Exception:
        return []


def save_history(history):
    try:
        HISTORY_FILE.write_text(json.dumps(history[-MAX_TURNS:], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


class ChatWorker(QObject):
    """Runs in a background thread so the pet never freezes while she thinks."""

    chunk = Signal(str)      # text piece as it streams in
    finished = Signal(str)   # the full reply
    failed = Signal(str)     # a friendly error message

    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def run(self):
        body = json.dumps({
            "model": MODEL,
            "messages": self.messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {"temperature": 0.8, "num_predict": 160},
        }).encode("utf-8")
        req = urllib.request.Request(HOST + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        full = ""
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise RuntimeError(data["error"])
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        full += piece
                        self.chunk.emit(full)
                    if data.get("done"):
                        break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.failed.emit(f"I don't have the model '{MODEL}' yet. Run:  ollama pull {MODEL}")
            else:
                self.failed.emit(f"My brain returned an error ({e.code}).")
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            self.failed.emit("I can't reach my brain. Is Ollama running?")
            return
        except Exception as e:
            self.failed.emit(f"Something broke in my head: {e}")
            return
        if not full.strip():
            self.failed.emit("...I had nothing to say. Try again?")
            return
        self.finished.emit(full)
