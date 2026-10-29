import math
import os
import random
import sys
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, Qt, QThread, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QMenu, QWidget

import brain
import windows_api
from chat_ui import ChatBox

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
    "sleepy": ("sleep", 6000, 12000),
    "sleep": ("stretch", 2200, 2200),
    "pounce": ("idle", 1500, 3000),
    "rage": ("idle", 1500, 2500),
    "chaos": ("idle", 1500, 3000),
}
# her mood tag -> which pose she makes for a few seconds
MOOD_STATE = {
    "happy": "laughing", "annoyed": "rage", "embarrassed": "blushing",
    "surprised": "surprised", "sleepy": "sleepy", "smug": "chaos",
    "sing": "music", "neutral": "wave",
}
BUSY = ("fall", "jump", "climb", "climbtop")   # moving through the air / on a wall: clicks and moods wait
NO_BOB = ("fall", "jump", "land", "crouch", "climb", "climbtop", "peek", "lie_down", "pounce")
FACES_DIRECTION = {"walk", "walk_2", "jump", "sit_edge", "crouch", "air", "climb", "climbtop"}   # mirrored when facing left

# frame-by-frame animations: <file prefix>_f01.png, _f02.png, ...  ->  (frames per second, how it plays)
ANIMS = {
    "crouch": (24, "once"),       # wind-up before a hop
    "jump": (24, "once"),         # take-off, first part of the arc
    "air": (12, "pingpong"),      # rest of the arc / floating (forward then backward = smooth loop)
    "fallstart": (24, "once"),    # the "uh oh" moment when a fall begins
    "fall": (24, "loop"),         # flailing while falling
    "land": (30, "once"),
    "drag": (24, "loop"),         # dangling while you hold her
    "climb": (36, "loop"),        # climbing up the side of a window
    "climbtop": (36, "once"),     # reaching the top, climbing over and standing up
    "dragbin": (24, "once"),      # shake her: she starts to panic
    "dragb": (24, "loop"),        # panicking while you keep shaking
    "dragbout": (24, "once"),     # you stop shaking: she gives up...
    "dragbl": (24, "pingpong"),   # ...and hangs there limp until you let go         # landing (drop land_f01.png, land_f02.png ... into sprites to use it)
}
SHAKE_WINDOW = 15                 # ticks (~0.5 s) of cursor movement looked at for shaking
SHAKE_START = 250                 # back-and-forth distance (px) in that window that makes her panic
SHAKE_CALM = 80                   # total movement (px) below which she starts calming down
XFADE_MS = 160                    # quick cross-fade between drag animations
CLIMB_SPEED = 1.13                # average px per tick going up a window's side (matches the video)
CLIMB_HAND = 35                   # px from her centre to the wall she is climbing
CLIMB_BLEND = 8                   # the last climb frames are a blend back to the start; the climb-over starts there instead
CLIMB_REACH = 100                 # she can hang this far below a window's bottom edge (feet below, hands on it)
CLIMB_JUMP = 300                  # highest jump up to grab the side of a floating window
CLIMB_TOP = 70                    # when her feet get this close to the top, she pulls herself up
CROUCH_MS = 270                   # how long she winds up before a hop
TAKEOFF_PART = 0.35               # first 35% of the arc uses the take-off frames

SIZE = 200                        # sprite size on screen (px)
HEADROOM = 40                     # spare space above her (bouncing, swinging)
MARGIN = 90                       # spare space left and right so swinging never clips her
GRAB_POINT = (MARGIN + SIZE / 2, HEADROOM + 6)   # where the cursor holds her while dragging (her pinched jacket)
FEET_PAD = round(16 / 413 * SIZE) # transparent gap under her feet in the sprite files
TICK_MS = 33                      # about 30 frames per second
WALK_SPEED = 2.0                  # pixels per tick, matched to her feet in the walk video
WALK_FRAME_MS = 1000 / 48         # walk frames play at 48 fps at normal speed
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
        self.setFixedSize(SIZE + 2 * MARGIN, SIZE + HEADROOM)

        # ---- sprites (missing new ones fall back to existing ones) ----
        self.frames = {}
        for n in BASE:
            self.frames[n] = self.load(SPRITES / f"{n}.png")
        for n, fallback in OPTIONAL.items():
            path = SPRITES / f"{n}.png"
            self.frames[n] = self.load(path) if path.exists() else self.frames[fallback]
        # animated walk: walk_f01.png, walk_f02.png, ... (from the video)
        self.walk_frames = []
        for num, path in self.numbered("walk"):
            key = f"walk@{num - 1}"
            self.frames[key] = self.load(path)
            self.walk_frames.append(key)
        # other animations from ANIMS (missing files = that animation is skipped)
        self.anims = {}
        for prefix in ANIMS:
            keyname = "landf" if prefix == "land" else prefix
            keys = []
            for num, path in self.numbered(prefix):         # gaps are fine: deleted frames are just skipped
                key = f"{keyname}@{num - 1}"                # keeps the original frame number
                self.frames[key] = self.load(path)
                keys.append(key)
            if keys:
                self.anims[prefix] = keys
        # climb_lift.txt: how strongly each climb frame pulls her up (so she rises with her arms, not a smooth slide)
        self.climb_lift = []
        try:
            self.climb_lift = [float(v) for v in (SPRITES / "climb_lift.txt").read_text().split()]
        except (OSError, ValueError):
            pass
        # climbtop_path.txt: where the window's corner is in each climb-over frame, so she moves exactly with it
        self.top_path, self.top_end, self.top_exit = [], None, None
        try:
            for line in (SPRITES / "climbtop_path.txt").read_text().splitlines():
                parts = line.split()
                if not parts or parts[0].startswith("#"):
                    continue
                if parts[0] == "end":
                    self.top_end = (float(parts[1]), float(parts[2]))
                elif parts[0] == "exit":                   # last climbing-loop frame before the climb-over
                    self.top_exit = int(parts[1])
                else:
                    self.top_path.append((float(parts[0]), float(parts[1])))
        except (OSError, ValueError, IndexError):
            self.top_path, self.top_end = [], None
        for n in list(self.frames):                 # mirrored copies, only for poses that face a direction
            if n.split("@")[0] in FACES_DIRECTION:
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
        self.speed = WALK_SPEED
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
        self.pending_jump = None
        self.climb = None
        self.climb_next = False
        self.drag_mode = "a"
        self.drag_t0 = 0
        self.shake = 0.0
        self.trail = []
        self.calm_ticks = 0
        self.want_out = False
        self.xfade = None
        self.land_kind = "fall"
        self.state_t0 = 0
        self.prev_state = None

        # ---- chat ----
        self.chat = None
        self.history = brain.load_history()
        self.job = None

        self.sync_window()
        self.refresh_platforms()
        self.set_state("wave", 2600, 2600)    # wave on launch

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)

    # ---------- helpers ----------
    @staticmethod
    def numbered(prefix):
        """All sprites/<prefix>_fNN.png files in number order, as (number, path)."""
        found = []
        for path in SPRITES.glob(f"{prefix}_f*.png"):
            digits = path.stem[len(prefix) + 2:]
            if digits.isdigit():
                found.append((int(digits), path))
        return sorted(found)

    @staticmethod
    def load(path):
        pm = QPixmap(str(path))
        # scale by width, so taller frames (extra room above her head) keep the same size as the rest
        return pm.scaled(SIZE, round(pm.height() * SIZE / max(pm.width(), 1)),
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def now_ms(self):
        return self.t * TICK_MS

    def sync_window(self):
        self.move(round(self.cx - SIZE / 2 - MARGIN),
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
        if self.state not in BUSY and not self.dragging:
            fn()

    def next_state(self):
        """Pick something to do. Short pauses so she is always up to something."""
        if self.chat is not None and self.chat.isVisible():
            self.set_state("idle", 1500, 3000)       # stay put while you talk to her
            return
        r = random.random()
        if r < 0.25:
            self.start_walk()
        elif r < 0.40:
            if not self.start_hop():
                self.start_walk()
        elif r < 0.52:                                   # about 1 time in 8: climb a window
            if not self.start_climb() and not self.start_hop():
                self.start_walk()
        elif r < 0.55:
            self.start_stalk()
        elif r < 0.60:
            self.set_state("sit", 4000, 8000)
        elif r < 0.65:
            self.set_state("phone", 5000, 9000)
        elif r < 0.69:
            self.set_state("coffee", 5000, 9000)
        elif r < 0.72:
            self.set_state("lie_down", 5000, 9000)
        elif r < 0.76:
            self.set_state("peek", 3000, 5000)
        elif r < 0.79:
            self.set_state("stretch", 2200, 2200)
        elif r < 0.83:
            self.set_state("wave", 2600, 2600)
        elif r < 0.85:
            self.set_state("chaos", 2200, 2800)
        elif r < 0.88:
            self.set_state("sleepy", 3000, 4000)
        else:
            self.set_state("idle", 1000, 3000)

    def start_stalk(self):
        """Sneak up on the mouse cursor and pounce on it."""
        p = self.current_platform()
        lo, hi = self.span(p)
        c = QCursor.pos()
        if lo <= c.x() <= hi and abs(c.y() - self.feet) < 350 and abs(c.x() - self.cx) > 60:
            self.step_off = False
            self.walk_target = float(c.x())
            self.pounce_next = True
            self.speed = WALK_SPEED * 2.0
            self.state = "walk"
        else:
            self.start_walk()

    def start_walk(self):
        p = self.current_platform()
        lo, hi = self.span(p)
        if hi - lo < 40:
            self.set_state("idle", 800, 1500)
            return
        self.step_off = False
        self.pounce_next = False
        self.climb_next = False
        self.speed = WALK_SPEED * (2.4 if random.random() < 0.25 else 1.0)   # sometimes a dash
        if self.plat_id and random.random() < 0.3:
            self.step_off = True                         # walk to the edge and drop off
            self.walk_target = random.choice([lo, hi])
        else:
            self.walk_target = random.uniform(lo, hi)
            if abs(self.walk_target - self.cx) < 250:      # go somewhere properly far
                self.walk_target = lo if self.cx - lo > hi - self.cx else hi
        self.state = "walk"

    def step_walk(self):
        p = self.current_platform()
        lo, hi = self.span(p)
        dx = self.walk_target - self.cx
        if abs(dx) <= self.speed:
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
            elif self.climb_next:
                self.climb_next = False
                self.begin_climb()
            else:
                self.set_state("idle", 500, 1500)
            return
        self.facing = 1 if dx > 0 else -1
        self.cx = clamp(self.cx + self.speed * self.facing, lo, hi)
        if self.plat_id:
            self.plat_off = self.cx - p["left"]
        self.sync_window()

    # ---------- climbing up the side of a window ----------
    def climb_target_feet(self, p):
        """Her feet height when the climb-over begins (or the window top if there's no climb-over)."""
        if self.has_climbtop():
            v = self.top_path[self.frame_no(self.anims["climbtop"][0])][1]
            return p["top"] + SIZE - FEET_PAD - v * SIZE / 413
        return p["top"] + CLIMB_TOP

    def start_climb(self):
        if self.plat_id != 0 or "climb" not in self.anims:
            return False
        floor = self.floor["top"]
        lo, hi = self.span(self.floor)
        options = []
        for p in self.platforms:
            if p["id"] == 0:
                continue
            start = min(floor, p["bottom"] + CLIMB_REACH)    # where she starts: the floor, or jumps up to here
            if floor - start > CLIMB_JUMP or start - self.climb_target_feet(p) < 40:
                continue                                   # too high to jump to, or too short to be worth climbing
            for side, edge in (("L", p["left"]), ("R", p["right"])):
                x = edge - CLIMB_HAND if side == "L" else edge + CLIMB_HAND
                if lo <= x <= hi and not self.covered(p, edge + (8 if side == "L" else -8)):
                    options.append((p, side, x, start))
        if not options:
            return False
        p, side, x, start = random.choice(options)
        self.start_walk()
        self.step_off = False
        self.walk_target = x
        self.climb = dict(pid=p["id"], side=side, scale=1.0, final=False, need_wrap=False, last_frame=-1,
                          start=start)
        self.climb_next = True
        return True

    def begin_climb(self):
        """At the foot of the window: start climbing, or jump up and grab its side first."""
        self.facing = 1 if self.climb["side"] == "L" else -1   # face the wall
        start = self.climb["start"]
        if self.feet - start < 10:
            self.state = "climb"
            return
        dist = self.feet - start
        self.jump = dict(sx=self.cx, sy=self.feet, tx=self.cx, ty=start, pid=self.climb["pid"],
                         t0=self.now_ms(), dur=clamp(450 + dist * 1.2, 600, 1000), h=40.0, grab=True)
        self.state = "jump"

    @staticmethod
    def frame_no(key):
        return int(key.split("@")[1])                       # original frame number (0-based)

    def has_climbtop(self):
        return ("climbtop" in self.anims and self.top_end is not None
                and all(self.frame_no(k) < len(self.top_path) for k in self.anims["climbtop"]))

    def corner_to_her(self, p, u, v):
        """Where she must be (cx, feet) so sprite pixel (u, v) lands on the window's top corner."""
        k = SIZE / 413
        if self.climb["side"] == "L":
            cx = p["left"] + SIZE / 2 - u * k
        else:                                              # mirrored sprite on the right side
            cx = p["right"] + SIZE / 2 - (413 - u) * k
        feet = p["top"] + SIZE - FEET_PAD - v * k
        return cx, feet

    def step_climb(self):
        p = self.find_platform(self.climb["pid"]) if self.climb else None
        if p is None:
            self.start_fall()                               # the window closed under her hands
            return
        c = self.climb
        left = c["side"] == "L"
        self.cx = p["left"] - CLIMB_HAND if left else p["right"] + CLIMB_HAND
        keys = self.anims.get("climb", [])
        total = len(self.climb_lift) or (max(map(self.frame_no, keys)) + 1 if keys else 0)
        lift, frame = 1.0, 0
        if keys:
            frame = self.frame_no(self.anim_frame("climb", self.now_ms() - self.state_t0))
            if frame < len(self.climb_lift):
                lift = self.climb_lift[frame]
        wrapped = frame < c["last_frame"] or c["last_frame"] < 0   # just started, or a cycle just finished
        c["last_frame"] = frame
        if self.has_climbtop():
            end = self.top_exit + 1 if self.top_exit is not None else total - CLIMB_BLEND   # where the climb-over takes over
            if wrapped and c["final"]:
                c["need_wrap"] = False
            if c["final"] and not c["need_wrap"] and frame >= end:
                self.climb_over(p)                          # hand over to the climb-over animation
                return
            if wrapped and not c["final"]:
                _, target = self.corner_to_her(p, *self.top_path[self.frame_no(self.anims["climbtop"][0])])
                def rise(frames):                           # how far a run of climb frames lifts her
                    lifts = [self.climb_lift[f] if f < len(self.climb_lift) else 1.0 for f in frames]
                    return CLIMB_SPEED * sum(lifts) * 30 / ANIMS["climb"][0]
                nums = list(map(self.frame_no, keys))
                to_exit = rise(f for f in nums if f < end)            # from the loop start to the exit frame
                full = rise(nums)                                     # one whole loop
                left_to_go = self.feet - target
                if left_to_go <= to_exit * 1.5:            # arrive this pass: stretch it to land exactly
                    c["final"], c["need_wrap"] = True, False
                    c["scale"] = max(left_to_go, 0) / max(to_exit, 0.01)
                elif left_to_go <= (full + to_exit) * 1.2:  # one more full loop, then the exit
                    c["final"], c["need_wrap"] = True, True
                    c["scale"] = left_to_go / (full + to_exit)
        self.feet -= CLIMB_SPEED * lift * c["scale"]
        if p["top"] > self.feet or p["bottom"] < self.feet - 150:
            self.start_fall()                               # the window moved away from her
            return
        if not self.has_climbtop() and self.feet <= p["top"] + CLIMB_TOP:
            tx = p["left"] + 50 if left else p["right"] - 50
            self.climb = None
            self.begin_jump(p, tx, soft=True)               # no climb-over animation: small hop up
            return
        self.sync_window()

    def climb_over(self, p):
        self.cx, self.feet = self.corner_to_her(p, *self.top_path[self.frame_no(self.anims["climbtop"][0])])
        self.state = "climbtop"
        self.state_t0 = self.now_ms()
        self.prev_state = "climbtop"
        self.sync_window()

    def step_climbtop(self):
        p = self.find_platform(self.climb["pid"]) if self.climb else None
        if p is None:
            self.start_fall()
            return
        el = self.now_ms() - self.state_t0
        keys = self.anims["climbtop"]
        n = len(keys)
        i = min(int(el * ANIMS["climbtop"][0] / 1000), n - 1)
        self.cx, self.feet = self.corner_to_her(p, *self.top_path[self.frame_no(keys[i])])
        if el * ANIMS["climbtop"][0] / 1000 >= n:           # finished: she's standing on top
            old = (self.current_frame(), self.x(), self.y())
            body_u, _ = self.top_end
            k = SIZE / 413
            u = body_u if self.climb["side"] == "L" else 413 - body_u
            self.cx = self.cx - SIZE / 2 + u * k
            self.climb = None
            self.land_on(p, soft=True)                       # stands on the window, then idles
            self.xfade = (old[0], self.now_ms(), old[1] - self.x(), old[2] - self.y())
            return
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

    def land_on(self, p, soft=False):
        self.land_kind = self.state          # "jump" or "fall": picks the right stand-in landing pose
        self.plat_id = p["id"]
        self.feet = p["top"]
        self.plat_off = self.cx - p["left"]
        self.vx = self.vy = 0.0
        if "land" in self.anims:
            ms = int(clamp(len(self.anims["land"]) * 1000 / ANIMS["land"][0], 250, 2500))
        else:
            ms = 250
        if soft:
            self.set_state("idle", 800, 1500)        # a gentle step up after climbing: no big landing
        else:
            self.set_state("land", ms, ms)
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
                    self.pending_jump = (p["id"], tx)      # crouch first, then jump
                    self.facing = 1 if tx > self.cx else -1
                    self.set_state("crouch", CROUCH_MS, CROUCH_MS)
                    return True
        return False

    def begin_jump(self, p, tx, soft=False):
        dist = math.hypot(tx - self.cx, p["top"] - self.feet)
        self.jump = dict(
            sx=self.cx, sy=self.feet, tx=tx, ty=p["top"], pid=p["id"],
            t0=self.now_ms(), dur=clamp(450 + dist * 0.9, 600, 1800),
            h=max(70.0, abs(p["top"] - self.feet) * 0.4 + 70.0), soft=soft,
        )
        if soft:                                  # small pull-up over the edge, not a big leap
            self.jump["h"] = 30.0
            self.jump["dur"] = 450
        self.facing = 1 if tx > self.cx else -1
        self.plat_id = 0
        self.state = "jump"

    def step_jump(self):
        j = self.jump
        u = (self.now_ms() - j["t0"]) / j["dur"]
        if u >= 1:
            self.jump = None
            p = self.find_platform(j["pid"])
            if j.get("grab"):                               # jumped up to grab a window's side
                if p is None or self.climb is None:
                    self.start_fall()
                    return
                self.feet = j["ty"]
                self.state = "climb"
                self.sync_window()
                return
            if p is None:
                self.cx, self.feet = j["tx"], j["ty"]
                self.start_fall()
            else:
                self.cx = clamp(j["tx"], p["left"] + 10, p["right"] - 10) if p["id"] else j["tx"]
                self.land_on(p, soft=j.get("soft", False))
            return
        self.cx = j["sx"] + (j["tx"] - j["sx"]) * u
        self.feet = j["sy"] + (j["ty"] - j["sy"]) * u - 4 * j["h"] * u * (1 - u)
        self.sync_window()

    # ---------- the heartbeat ----------
    def tick(self):
        self.t += 1
        interval = 4 if (self.plat_id or self.state in ("jump", "climb", "climbtop")) else 12
        if self.t % interval == 0 and not self.dragging:
            self.refresh_platforms()

        if self.dragging:
            if "drag" in self.anims:
                # slide her so the pinched jacket ends up right under the cursor
                gx, gy = GRAB_POINT
                self.drag_offset[0] += (gx - self.drag_offset[0]) * 0.3
                self.drag_offset[1] += (gy - self.drag_offset[1]) * 0.3
                self.place_under_cursor()
            self.step_drag_mode()
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
        elif self.state == "climb":
            self.step_climb()
        elif self.state == "climbtop":
            self.step_climbtop()
        elif self.now_ms() >= self.state_until:
            if self.state == "crouch":
                self.finish_crouch()
            elif self.state in FOLLOW:
                self.set_state(*FOLLOW[self.state])
            else:
                self.next_state()

        if self.state == "idle" and self.now_ms() >= self.next_blink:
            self.blink_until = self.now_ms() + 160
            self.next_blink = self.now_ms() + random.randint(2500, 6000)
        if self.state != self.prev_state:          # remember when each state started (for animations)
            self.prev_state = self.state
            self.state_t0 = self.now_ms()
        self.follow_chat()
        self.update()

    # ---------- drag_a (calm) / drag_b (panic) ----------
    DRAG_ANIM = {"a": "drag", "b_in": "dragbin", "b": "dragb", "b_out": "dragbout", "limp": "dragbl"}

    def set_drag_mode(self, mode, fade=False):
        if fade:
            self.xfade = (self.current_frame(), self.now_ms(), 0, 0)
        self.drag_mode = mode
        self.drag_t0 = self.now_ms()

    def anim_done(self, name, elapsed):
        return elapsed >= len(self.anims[name]) * 1000 / ANIMS[name][0]

    def measure_shake(self):
        """Shaking = lots of cursor movement that doesn't go anywhere (back and forth)."""
        self.trail.append(self.drag_prev)
        if len(self.trail) > SHAKE_WINDOW:
            self.trail.pop(0)
        path = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(self.trail, self.trail[1:]))
        first, last = self.trail[0], self.trail[-1]
        net = math.hypot(last[0] - first[0], last[1] - first[1])
        self.shake = path - net
        return path

    def step_drag_mode(self):
        path = self.measure_shake()
        if not all(k in self.anims for k in ("dragbin", "dragb", "dragbout", "dragbl")):
            return
        m = self.drag_mode
        el = self.now_ms() - self.drag_t0
        if m == "a" and self.shake > SHAKE_START:
            self.set_drag_mode("b_in", fade=True)
        elif m == "b_in" and self.anim_done("dragbin", el):
            self.set_drag_mode("b")
        elif m == "b":
            self.calm_ticks = self.calm_ticks + 1 if path < SHAKE_CALM else 0
            if self.calm_ticks >= 10:
                self.want_out = True
            n = len(self.anims["dragb"])
            if self.want_out and int(el * ANIMS["dragb"][0] / 1000) % n == n - 1:
                self.want_out = False                       # leave at the end of a loop = no jump
                self.set_drag_mode("b_out")
        elif m == "b_out" and self.anim_done("dragbout", el):
            self.set_drag_mode("limp")
        elif m == "limp" and self.shake > SHAKE_START:
            self.calm_ticks = 0
            self.set_drag_mode("b", fade=True)

    def finish_crouch(self):
        target = self.pending_jump
        self.pending_jump = None
        p = self.find_platform(target[0]) if target else None
        if p is None:
            self.set_state("idle", 800, 1500)        # the window went away while she crouched
            return
        lo, hi = self.span(p)
        self.begin_jump(p, clamp(target[1], lo, hi))

    def anim_frame(self, name, elapsed_ms):
        keys = self.anims[name]
        fps, mode = ANIMS[name]
        i = int(max(elapsed_ms, 0) * fps / 1000)
        n = len(keys)
        if mode == "once":
            i = min(i, n - 1)
        elif mode == "pingpong" and n > 1:
            period = 2 * n - 2
            i %= period
            if i >= n:
                i = period - i
        else:
            i %= n
        return keys[i]

    # ---------- drawing ----------
    def sprite_name(self):
        s = self.state
        ms = self.now_ms()
        if s == "wave":
            return "wave" if (ms // 350) % 2 == 0 else "idle"
        if s == "walk" and self.walk_frames:
            frame_ms = WALK_FRAME_MS * WALK_SPEED / max(self.speed, 0.1)   # faster walk = faster legs
            return self.walk_frames[int(ms // frame_ms) % len(self.walk_frames)]
        if s == "walk":
            return "walk" if (ms // (180 if self.speed <= WALK_SPEED else 100)) % 2 == 0 else "walk_2"
        elapsed = ms - self.state_t0
        if s == "climb":
            return self.anim_frame("climb", elapsed) if "climb" in self.anims else "climb"
        if s == "climbtop" and "climbtop" in self.anims:
            return self.anim_frame("climbtop", elapsed)
        if s == "crouch":
            return self.anim_frame("crouch", elapsed) if "crouch" in self.anims else "land"
        if s == "jump" and self.jump is not None:
            u = (ms - self.jump["t0"]) / self.jump["dur"]
            if u < TAKEOFF_PART and "jump" in self.anims:
                return self.anim_frame("jump", elapsed)
            if "air" in self.anims:
                return self.anim_frame("air", elapsed - self.jump["dur"] * TAKEOFF_PART)
            return "jump"
        if s == "land":
            if "land" in self.anims:
                return self.anim_frame("land", elapsed)
            if self.land_kind == "jump" and "crouch" in self.anims:
                return self.anim_frame("crouch", elapsed)   # stand-in until the landing animation exists
            return "land"
        if s == "fall":
            start_ms = 0
            if "fallstart" in self.anims:
                start_ms = len(self.anims["fallstart"]) * 1000 / ANIMS["fallstart"][0]
                if elapsed < start_ms:
                    return self.anim_frame("fallstart", elapsed)
            if "fall" in self.anims:
                return self.anim_frame("fall", elapsed - start_ms)
            return "fall"
        if s == "drag":
            name = self.DRAG_ANIM.get(self.drag_mode, "drag")
            if name in self.anims:
                return self.anim_frame(name, ms - self.drag_t0)
            if "drag" in self.anims:
                return self.anim_frame("drag", elapsed)
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
        if name.split("@")[0] in FACES_DIRECTION and self.facing == -1:
            name += "_L"
        return self.frames[name]

    def paintEvent(self, _):
        sec = self.now_ms() / 1000
        p = QPainter(self)
        p.translate(MARGIN, 0)                     # her drawing area sits in the middle of the window
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        pm = self.current_frame()
        if self.state == "drag":
            # she hangs from the cursor and swings like a pendulum
            p.translate(SIZE / 2, HEADROOM)
            p.rotate(self.sway + math.sin(sec * 3) * 3)
            p.drawPixmap(-SIZE // 2, 0, pm)
            if self.xfade is not None:                      # fade out the previous animation's frame
                old, t0, _, _ = self.xfade
                k = (self.now_ms() - t0) / XFADE_MS
                if k >= 1:
                    self.xfade = None
                else:
                    p.setOpacity(1 - k)
                    p.drawPixmap(-SIZE // 2, 0, old)
            return
        if self.state == "music" and self.sprite_name() == "headbang":
            # headbang: rock from the feet and bounce to the beat
            p.translate(SIZE / 2, HEADROOM + SIZE)
            p.rotate(math.sin(sec * 9) * 4)
            p.drawPixmap(-SIZE // 2, int(-SIZE - abs(math.sin(sec * 9)) * 6), pm)
            return
        if self.state == "walk":
            bob = 0 if self.walk_frames else -abs(math.sin(sec * 9)) * 7
        elif self.state == "music":
            bob = -abs(math.sin(sec * 6)) * 5
        elif self.state == "sleep":
            bob = math.sin(sec * 1.2) * 1.5
        elif self.state in NO_BOB:
            bob = 0
        else:
            bob = math.sin(sec * 2) * 2     # gentle breathing
        p.drawPixmap(0, int(HEADROOM + SIZE - pm.height() + bob), pm)   # feet always at the same spot
        if self.xfade is not None:                          # fade out the last frame of the previous animation
            old, t0, dx, dy = self.xfade
            k = (self.now_ms() - t0) / (XFADE_MS * 2)
            if k >= 1:
                self.xfade = None
            else:
                p.setOpacity(1 - k)
                p.drawPixmap(int(dx), int(HEADROOM + SIZE - old.height() + dy), old)

    # ---------- mouse ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pressed = True
            self.press_pos = e.globalPosition()
            self.drag_prev = (e.globalPosition().x(), e.globalPosition().y())
            off = e.globalPosition() - self.frameGeometry().topLeft().toPointF()
            self.drag_offset = [off.x(), off.y()]     # eases towards GRAB_POINT once she's picked up

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
            self.drag_mode = "a"
            self.drag_t0 = self.now_ms()
            self.shake = 0.0
            self.trail = []
            self.calm_ticks = 0
            self.want_out = False
            self.xfade = None
        self.mvx = clamp(self.mvx + (pos.x() - self.drag_prev[0]) * 0.5, -40, 40)
        self.mvy = clamp(self.mvy + (pos.y() - self.drag_prev[1]) * 0.5, -40, 40)
        self.drag_prev = (pos.x(), pos.y())
        self.place_under_cursor()

    def place_under_cursor(self):
        x = self.drag_prev[0] - self.drag_offset[0]
        y = self.drag_prev[1] - self.drag_offset[1]
        self.move(round(x), round(y))
        self.cx = x + MARGIN + SIZE / 2
        self.feet = y + SIZE + HEADROOM - FEET_PAD

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

    # ---------- chat ----------
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.open_chat()

    def open_chat(self):
        if self.chat is None:
            self.chat = ChatBox()
            self.chat.submitted.connect(self.send_chat)
            self.chat.load(self.history)
        self.follow_chat()
        self.chat.open_box()
        if not self.chat.log:
            self.set_state("wave", 2600, 2600)

    def follow_chat(self):
        c = self.chat
        if c is None or not c.isVisible():
            return
        x = int(self.cx - c.width() / 2)
        y = int(self.y() + HEADROOM - c.height() + 10)     # above her head
        if y < self.geo.top():
            y = int(self.feet + 8)                         # no room above: go below
        x = int(clamp(x, self.geo.left(), self.geo.right() - c.width()))
        y = int(clamp(y, self.geo.top(), self.geo.bottom() - c.height()))
        if (x, y) != (c.x(), c.y()):
            c.move(x, y)

    def send_chat(self, text):
        if self.job is not None:
            return
        self.history.append({"role": "user", "content": text})
        self.chat.add_user(text)
        self.chat.set_busy(True)
        if self.state not in BUSY and not self.dragging:
            self.set_state("phone", 120000, 120000)          # "typing a reply"

        thread = QThread()
        worker = brain.ChatWorker(brain.build_messages(self.history))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.chunk.connect(self.chat_chunk)
        worker.finished.connect(self.chat_done)
        worker.failed.connect(self.chat_failed)
        for sig in (worker.finished, worker.failed):
            sig.connect(thread.quit)
        thread.finished.connect(self.chat_cleanup)
        self.job = (thread, worker)
        thread.start()

    def chat_chunk(self, full):
        self.chat.set_reply(brain.visible_text(full))

    def chat_done(self, full):
        mood, clean = brain.split_mood(full)
        self.history.append({"role": "assistant", "content": clean})
        brain.save_history(self.history)
        self.chat.set_reply(clean)
        self.chat.set_busy(False)
        if self.state not in BUSY and not self.dragging:
            self.set_state(MOOD_STATE.get(mood, "wave"), 3500, 3500)
        if self.chat.isVisible():
            self.chat.input.setFocus()

    def chat_failed(self, msg):
        self.history.pop()                  # drop the unanswered message
        self.chat.set_error(msg)
        self.chat.set_busy(False)
        if self.state == "phone":
            self.set_state("annoyed", 2500, 2500)

    def chat_cleanup(self):
        self.job = None

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("Chat with her", self.open_chat)
        menu.addAction("Play music", lambda: self.command(self.play_music))
        menu.addAction("Wave", lambda: self.command(lambda: self.set_state("wave", 2600, 2600)))
        menu.addAction("Walk somewhere", lambda: self.command(self.start_walk))
        menu.addAction("Hop to a window", lambda: self.command(self.start_hop))
        menu.addAction("Climb a window", lambda: self.command(self.start_climb))
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
