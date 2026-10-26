import math
import os
import random
import sys
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QMenu, QWidget

import windows_api

SPRITES = Path(__file__).parent / "sprites"

# sprites you already have
BASE = ["idle", "walk", "wave", "sit", "sleep", "music",
        "surprised", "annoyed", "laughing", "blushing"]

# new sprites: name -> which existing sprite to use until the real file exists
OPTIONAL = {
    "drag_a": "surprised",
    "drag_b": "drag_a",
    "fall": "surprised",
    "jump": "walk",
    "land": "idle",
    "sit_edge": "sit",
    "walk_2": "walk",
    "blink": "idle",
    "rage": "annoyed",
    "sleepy": "idle",
    "phone": "idle",
    "coffee": "sit",
    "sing": "music",
    "headbang": "music",
    "chaos": "laughing",
    "lie_down": "sleep",
    "pounce": "walk",
    "stretch": "idle",
    "peek": "idle",
    "climb": "walk",
}
# states that go straight into another state when their timer ends
FOLLOW = {
    "sleepy": ("sleep", 10000, 25000),
    "sleep": ("stretch", 2200, 2200),
    "pounce": ("idle", 1500, 3000),
    "rage": ("idle", 1500, 2500),
    "chaos": ("idle", 1500, 3000),
}
NO_BOB = ("fall", "jump", "land", "peek", "lie_down", "pounce")
FACES_DIRECTION = {"walk", "walk_2", "jump", "sit_edge"}   # mirrored when facing left

SIZE = 200                        # sprite size on screen (px)
HEADROOM = 24                     # spare space above her for bouncing
FEET_PAD = round(16 / 413 * SIZE) # transparent gap under her feet in the sprite files
TICK_MS = 33                      # about 30 frames per second
WALK_SPEED = 2.2                  # pixels per tick
GRAVITY = 0.8
MAX_FALL = 24
MIN_PLATFORM_W = 160              # ignore windows narrower than this

# ---- Spotify ----
MUSIC_URIS = [
    "spotify:album:4muEF5biWb506ZojGMfHb7",
]
AUTOPLAY = True


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Pet(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(SIZE, SIZE + HEADROOM)

        # ---- sprites (missing new ones fall back to existing ones) ----
        self.frames = {}
        for n in BASE:
            self.frames[n] = self.load(SPRITES / f"{n}.png")
        for n, fallback in OPTIONAL.items():
            path = SPRITES / f"{n}.png"
            self.frames[n] = self.load(path) if path.exists() else self.frames[fallback]
        for n in list(self.frames):
            self.frames[n + "_L"] = self.frames[n].transformed(QTransform().scale(-1, 1))

        # ---- screen ----
        screen = QGuiApplication.primaryScreen()
        self.dpr = screen.devicePixelRatio()
        self.geo = screen.availableGeometry()
        self.floor = dict(id=0, left=self.geo.left(), right=self.geo.right(),
                          top=self.geo.bottom() + 1, bottom=self.geo.bottom() + 1, z=-1)
        self.windows = []
        self.platforms = [self.floor]
        self.use_windows = True

        # ---- position: cx = centre of her, feet = y of her feet ----
        self.cx = float(self.geo.right() - SIZE * 0.6)
        self.feet = float(self.floor["top"])
        self.plat_id = 0          # 0 = floor, otherwise the window she stands on
        self.plat_off = 0.0       # how far from that window's left edge she stands
        self.vx = 0.0
        self.vy = 0.0
        self.facing = 1           # 1 = right, -1 = left
        self.walk_target = self.cx
        self.step_off = False
        self.jump = None
        self.no_land_id = None
        self.no_land_until = 0

        # ---- mouse ----
        self.pressed = False
        self.dragging = False
        self.press_pos = None
        self.drag_offset = None
        self.drag_prev = None
        self.mvx = 0.0
        self.mvy = 0.0
        self.sway = 0.0

        # ---- time ----
        self.t = 0
        self.state = "idle"
        self.state_until = 0
        self.blink_until = 0
        self.next_blink = 2000
        self.clicks = []
        self.pounce_next = False

        self.sync_window()
        self.refresh_platforms()
        self.set_state("wave", 2600, 2600)    # wave on launch

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)

    # ---------- helpers ----------
    @staticmethod
    def load(path):
        pm = QPixmap(str(path))
        return pm.scaled(SIZE, SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def now_ms(self):
        return self.t * TICK_MS

    def sync_window(self):
        self.move(round(self.cx - SIZE / 2),
                  round(self.feet - SIZE - HEADROOM + FEET_PAD))

    def find_platform(self, pid):
        for p in self.platforms:
            if p["id"] == pid:
                return p
        return None

    def current_platform(self):
        return self.find_platform(self.plat_id) or self.floor

    def span(self, p):
        """Left and right limits for her centre on a platform."""
        m = SIZE * 0.3 if p["id"] == 0 else 20
        lo = max(p["left"], self.geo.left()) + m
        hi = min(p["right"], self.geo.right()) - m
        if hi < lo:
            lo = hi = (lo + hi) / 2
        return lo, hi

    def covered(self, p, x):
        """True if another window hides this platform at x."""
        if p["z"] < 0:
            return False
        y = p["top"] + 2
        for w in self.windows[: p["z"]]:
            if w["left"] <= x <= w["right"] and w["top"] <= y <= w["bottom"]:
                return True
        return False

    # ---------- windows she can stand on ----------
    def refresh_platforms(self):
        self.windows = []
        if self.use_windows:
            for i, w in enumerate(windows_api.visible_windows()):
                l, t, r, b = (v / self.dpr for v in w.rect)
                self.windows.append(dict(id=w.hwnd, left=l, right=r, top=t, bottom=b, z=i))

        min_top = self.geo.top() + SIZE * 0.7
        max_top = self.floor["top"] - 60
        usable = []
        for w in self.windows:
            if w["right"] - w["left"] < MIN_PLATFORM_W or w["bottom"] - w["top"] < 60:
                continue
            if not (min_top <= w["top"] <= max_top):
                continue
            if w["right"] <= self.geo.left() + MIN_PLATFORM_W:
                continue
            if w["left"] >= self.geo.right() - MIN_PLATFORM_W:
                continue
            usable.append(w)
        self.platforms = [self.floor] + usable

        # ride along with the window she is standing on
        if self.plat_id and not self.dragging and self.state not in ("jump", "fall"):
            p = self.find_platform(self.plat_id)
            if p is None:
                self.start_fall()
                return
            self.cx = p["left"] + self.plat_off
            self.feet = p["top"]
            outside = not (p["left"] - 10 <= self.cx <= p["right"] + 10)
            if outside or self.covered(p, self.cx):
                self.start_fall()
            self.sync_window()

    # ---------- state machine ----------
    def set_state(self, name, min_ms, max_ms):
        self.state = name
        self.state_until = self.now_ms() + random.randint(min_ms, max_ms)

    def command(self, fn):
        """Run a menu action only when she is not in the air or being held."""
        if self.state not in ("fall", "jump") and not self.dragging:
            fn()

    def next_state(self):
        r = random.random()
        if r < 0.30:
            self.start_walk()
        elif r < 0.55:
            if not self.start_hop():
                self.start_walk()
        elif r < 0.62:
            self.set_state("sit", 6000, 15000)
        elif r < 0.67:
            self.set_state("sleepy", 3000, 4000)
        elif r < 0.72:
            self.set_state("wave", 2600, 2600)
        elif r < 0.76:
            self.set_state("phone", 8000, 16000)
        elif r < 0.80:
            self.set_state("coffee", 8000, 16000)
        elif r < 0.84:
            self.set_state("lie_down", 8000, 16000)
        elif r < 0.87:
            self.set_state("peek", 4000, 8000)
        elif r < 0.90:
            self.set_state("stretch", 2200, 2200)
        elif r < 0.93:
            self.start_stalk()
        elif r < 0.94:
            self.set_state("chaos", 2200, 2800)
        else:
            self.set_state("idle", 3000, 7000)

    def start_stalk(self):
        """Sneak up on the mouse cursor and pounce on it."""
        p = self.current_platform()
        lo, hi = self.span(p)
        c = QCursor.pos()
        if lo <= c.x() <= hi and abs(c.y() - self.feet) < 350 and abs(c.x() - self.cx) > 60:
            self.step_off = False
            self.walk_target = float(c.x())
            self.pounce_next = True
            self.state = "walk"
        else:
            self.start_walk()

    def start_walk(self):
        p = self.current_platform()
        lo, hi = self.span(p)
        if hi - lo < 40:
            self.set_state("idle", 2000, 4000)
            return
        self.step_off = False
        self.pounce_next = False
        if self.plat_id and random.random() < 0.3:
            self.step_off = True                         # walk to the edge and drop off
            self.walk_target = random.choice([lo, hi])
        else:
            self.walk_target = random.uniform(lo, hi)
            if abs(self.walk_target - self.cx) < 80:
                self.walk_target = lo if self.cx - lo > hi - self.cx else hi
        self.state = "walk"

    def step_walk(self):
        p = self.current_platform()
        lo, hi = self.span(p)
        dx = self.walk_target - self.cx
        if abs(dx) <= WALK_SPEED:
            self.cx = self.walk_target
            if self.plat_id:
                self.plat_off = self.cx - p["left"]
            self.sync_window()
            if self.step_off:
                self.step_off = False
                self.start_fall(self.facing * 1.5)
            elif self.pounce_next:
                self.pounce_next = False
                self.set_state("pounce", 900, 900)
            else:
                self.set_state("idle", 2000, 5000)
            return
        self.facing = 1 if dx > 0 else -1
        self.cx = clamp(self.cx + WALK_SPEED * self.facing, lo, hi)
        if self.plat_id:
            self.plat_off = self.cx - p["left"]
        self.sync_window()

    # ---------- falling ----------
    def start_fall(self, vx=0.0, vy=0.0):
        self.no_land_id = self.plat_id
        self.no_land_until = self.t + 15
        self.plat_id = 0
        self.jump = None
        self.vx = vx
        self.vy = vy
        self.state = "fall"

    def land_on(self, p):
        self.plat_id = p["id"]
        self.feet = p["top"]
        self.plat_off = self.cx - p["left"]
        self.vx = self.vy = 0.0
        self.set_state("land", 250, 250)
        self.sync_window()

    def step_fall(self):
        prev = self.feet
        self.vy = min(self.vy + GRAVITY, MAX_FALL)
        self.feet += self.vy
        self.cx = clamp(self.cx + self.vx, self.geo.left() + 20, self.geo.right() - 20)
        self.vx *= 0.98

        best = None
        for p in self.platforms:
            if p["id"] == self.no_land_id and self.t < self.no_land_until:
                continue
            if not (p["left"] - 10 <= self.cx <= p["right"] + 10):
                continue
            if prev <= p["top"] + 1 and self.feet >= p["top"] and not self.covered(p, self.cx):
                if best is None or p["top"] < best["top"]:
                    best = p
        if best is None and self.feet >= self.floor["top"]:
            best = self.floor
        if best is not None:
            self.land_on(best)
        else:
            self.sync_window()

    # ---------- hopping between windows ----------
    def start_hop(self):
        cands = [p for p in self.platforms if p["id"] != self.plat_id]
        random.shuffle(cands)
        for p in cands:
            lo, hi = self.span(p)
            for _ in range(6):
                tx = random.uniform(lo, hi)
                if not self.covered(p, tx):
                    self.begin_jump(p, tx)
                    return True
        return False

    def begin_jump(self, p, tx):
        dist = math.hypot(tx - self.cx, p["top"] - self.feet)
        self.jump = dict(
            sx=self.cx, sy=self.feet, tx=tx, ty=p["top"], pid=p["id"],
            t0=self.now_ms(), dur=clamp(450 + dist * 0.9, 600, 1800),
            h=max(70.0, abs(p["top"] - self.feet) * 0.4 + 70.0),
        )
        self.facing = 1 if tx > self.cx else -1
        self.plat_id = 0
        self.state = "jump"

    def step_jump(self):
        j = self.jump
        u = (self.now_ms() - j["t0"]) / j["dur"]
        if u >= 1:
            self.jump = None
            p = self.find_platform(j["pid"])
            if p is None:
                self.cx, self.feet = j["tx"], j["ty"]
                self.start_fall()
            else:
                self.cx = clamp(j["tx"], p["left"] + 10, p["right"] - 10) if p["id"] else j["tx"]
                self.land_on(p)
            return
        self.cx = j["sx"] + (j["tx"] - j["sx"]) * u
        self.feet = j["sy"] + (j["ty"] - j["sy"]) * u - 4 * j["h"] * u * (1 - u)
        self.sync_window()

    # ---------- the heartbeat ----------
    def tick(self):
        self.t += 1
        interval = 4 if (self.plat_id or self.state == "jump") else 12
        if self.t % interval == 0 and not self.dragging:
            self.refresh_platforms()

        if self.dragging:
            target = clamp(self.mvx * 1.5, -25, 25)
            self.sway += (target - self.sway) * 0.2
            self.mvx *= 0.8
            self.mvy *= 0.8
        elif self.state == "fall":
            self.step_fall()
        elif self.state == "jump":
            self.step_jump()
        elif self.state == "walk":
            self.step_walk()
        elif self.now_ms() >= self.state_until:
            if self.state in FOLLOW:
                self.set_state(*FOLLOW[self.state])
            else:
                self.next_state()

        if self.state == "idle" and self.now_ms() >= self.next_blink:
            self.blink_until = self.now_ms() + 160
            self.next_blink = self.now_ms() + random.randint(2500, 6000)
        self.update()

    # ---------- drawing ----------
    def sprite_name(self):
        s = self.state
        ms = self.now_ms()
        if s == "wave":
            return "wave" if (ms // 350) % 2 == 0 else "idle"
        if s == "walk":
            return "walk" if (ms // 180) % 2 == 0 else "walk_2"
        if s == "drag":
            return "drag_a" if (ms // 220) % 2 == 0 else "drag_b"
        if s == "sit":
            return "sit_edge" if self.plat_id else "sit"
        if s == "music":
            return "sing" if (ms // 4000) % 2 == 0 else "headbang"
        if s == "idle" and ms < self.blink_until:
            return "blink"
        return s

    def current_frame(self):
        name = self.sprite_name()
        if name in FACES_DIRECTION and self.facing == -1:
            name += "_L"
        return self.frames[name]

    def paintEvent(self, _):
        sec = self.now_ms() / 1000
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        pm = self.current_frame()
        if self.state == "drag":
            # she hangs from the cursor and swings like a pendulum
            p.translate(SIZE / 2, HEADROOM)
            p.rotate(self.sway + math.sin(sec * 3) * 3)
            p.drawPixmap(-SIZE // 2, 0, pm)
            return
        if self.state == "music" and self.sprite_name() == "headbang":
            # headbang: rock from the feet and bounce to the beat
            p.translate(SIZE / 2, HEADROOM + SIZE)
            p.rotate(math.sin(sec * 9) * 4)
            p.drawPixmap(-SIZE // 2, int(-SIZE - abs(math.sin(sec * 9)) * 6), pm)
            return
        if self.state == "walk":
            bob = -abs(math.sin(sec * 9)) * 7
        elif self.state == "music":
            bob = -abs(math.sin(sec * 6)) * 5
        elif self.state == "sleep":
            bob = math.sin(sec * 1.2) * 1.5
        elif self.state in NO_BOB:
            bob = 0
        else:
            bob = math.sin(sec * 2) * 2     # gentle breathing
        p.drawPixmap(0, int(HEADROOM + bob), pm)

    # ---------- mouse ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pressed = True
            self.press_pos = e.globalPosition()
            self.drag_prev = (e.globalPosition().x(), e.globalPosition().y())
            self.drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if not self.pressed:
            return
        pos = e.globalPosition()
        if not self.dragging:
            if (pos - self.press_pos).manhattanLength() < 5:
                return
            self.dragging = True            # picked up
            self.plat_id = 0
            self.jump = None
            self.step_off = False
            self.vx = self.vy = 0.0
            self.mvx = self.mvy = 0.0
            self.sway = 0.0
            self.state = "drag"
        self.mvx = clamp(self.mvx + (pos.x() - self.drag_prev[0]) * 0.5, -40, 40)
        self.mvy = clamp(self.mvy + (pos.y() - self.drag_prev[1]) * 0.5, -40, 40)
        self.drag_prev = (pos.x(), pos.y())
        top_left = pos.toPoint() - self.drag_offset
        self.move(top_left)
        self.cx = top_left.x() + SIZE / 2
        self.feet = top_left.y() + SIZE + HEADROOM - FEET_PAD

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self.pressed:
            return
        self.pressed = False
        if self.dragging:
            self.dragging = False
            self.start_fall(clamp(self.mvx * 0.6, -10, 10), clamp(self.mvy * 0.6, -10, 6))
        else:
            # a plain click (ignored while she is in the air)
            self.command(self.on_click)

    def on_click(self):
        now = self.now_ms()
        self.clicks = [c for c in self.clicks if now - c < 3000] + [now]
        if self.state in ("sleep", "sleepy") or len(self.clicks) >= 4:
            self.clicks = []
            self.set_state("rage", 2600, 2600)      # woken up or spam-clicked
        else:
            self.set_state("laughing", 1500, 1500)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("Play music", lambda: self.command(self.play_music))
        menu.addAction("Wave", lambda: self.command(lambda: self.set_state("wave", 2600, 2600)))
        menu.addAction("Walk somewhere", lambda: self.command(self.start_walk))
        menu.addAction("Hop to a window", lambda: self.command(self.start_hop))
        menu.addAction("Sit", lambda: self.command(lambda: self.set_state("sit", 8000, 12000)))
        menu.addAction("Sleep", lambda: self.command(lambda: self.set_state("sleepy", 3000, 4000)))
        menu.addAction("Phone break", lambda: self.command(lambda: self.set_state("phone", 8000, 12000)))
        menu.addAction("Coffee break", lambda: self.command(lambda: self.set_state("coffee", 8000, 12000)))
        menu.addAction("Chase my mouse", lambda: self.command(self.start_stalk))
        menu.addSeparator()
        act = menu.addAction("Stand on my windows")
        act.setCheckable(True)
        act.setChecked(self.use_windows)
        act.toggled.connect(self.toggle_windows)
        menu.addSeparator()
        menu.addAction("Quit", lambda: QApplication.quit())
        menu.exec(e.globalPos())

    def toggle_windows(self, on):
        self.use_windows = on
        self.refresh_platforms()

    # ---------- music ----------
    def play_music(self):
        uri = random.choice(MUSIC_URIS)
        if "PASTE_YOURS_HERE" in uri:
            self.set_state("annoyed", 2500, 2500)
            return
        if AUTOPLAY:
            uri += "?play=true"
        try:
            os.startfile(uri)                      # opens Spotify on that item
        except (OSError, AttributeError):
            self.set_state("annoyed", 2500, 2500)
            return
        self.set_state("music", 30000, 60000)


def main():
    app = QApplication(sys.argv)

    lock = QLockFile(QDir.temp().filePath("ado_mate.lock"))
    if not lock.tryLock(100):
        sys.exit(0)          # she's already running, so do nothing

    pet = Pet()
    pet.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
