"""BMO Macro Buddy overlay for macro.py (v3).

Hotkeys: F6 click-through | F7 hide/show BMO | F8 finish placement (macro.py) |
         F10 pause/resume | Ctrl+F10 stop      (F9 opens Roblox's console, F11 is fullscreen: avoided)
Keep this file beside macro.py and your templates / placements folders.

v3 adds: stuck watchdog + Discord/Windows alerts, stop conditions (runs / hours / out of milk),
chained plans, a setup check, saved stats and window position, and a full log file (bmo_log.txt).

v4 adds: a focus guard (input is held while Roblox isn't the active window), keep-awake, live settings,
typed events from the macro, run history, and the TOOLS window (template tester + capture, routine
editor, placements, profiles, remote web page). The TOOLS code lives in bmo_tools.py; shared bits in
bmo_common.py. Keep all three .py files together.

Why v1 froze (fixed here):
  * click-through / capture-exclusion were applied to Tk's *child* window handle, not
    the real top-level window, which left the overlay half-layered (unclickable, no repaint)
  * pynput's hotkey thread called into Tk directly (root.after) - Tk is not thread-safe,
    and that is what produced the "alloc: invalid block" Tcl crash
  * every redraw registered new Tk click handlers (they were never freed), and Tk
    variables could be garbage-collected on the wrong thread when Settings closed
"""
import ctypes, gc, json, os, queue, re, secrets, subprocess, sys, threading, time, tkinter as tk, traceback
import urllib.error, urllib.request
from collections import Counter, deque
from tkinter import ttk
from ctypes import wintypes
from pathlib import Path

import bmo_common as common
import bmo_theme as theme
from bmo_tools import ToolsMixin

try:
    import bmo_calc
except Exception:       # the calculator is optional: BMO still runs without bmo_calc.py
    bmo_calc = None

try:
    from pynput import keyboard
except Exception:
    keyboard = None

HERE = Path(__file__).resolve().parent
MACRO = HERE / "macro.py"
SETTINGS_FILE = HERE / "bmo_settings.json"
ERROR_FILE = HERE / "bmo_error.log"
STATS_FILE = HERE / "bmo_stats.json"
LOG_FILE = HERE / "bmo_log.txt"
DEBUG_DIR = HERE / "debug"
UI_VERSION = 2
WATCHDOG_TRIES = 3          # recovery attempts in a row before BMO gives up and stops the macro
LOG_MAX_BYTES = 2_000_000   # bmo_log.txt rolls over to bmo_log.old.txt at this size

C = {"body": "#55B8B0", "dark": "#287E80", "light": "#9CE0D5", "screen": "#DDF7E9",
     "ink": "#315B54", "cream": "#FFF0C6", "blue": "#286E91", "red": "#A63848",
     "green": "#327C4B", "yellow": "#F7D56B", "white": "#FFFFFF", "amber": "#9A6B12"}
DEFAULTS = {"opacity": .96, "always_on_top": True, "capture_exclusion": True,
            "auto_clickthrough": False, "threshold": .80, "match_timeout": 15,
            "upgrade_every": 2.0, "wave_every": 1.5, "target_wave": 32, "wait_scale": 0.8,
            "mode": 0, "ui_version": UI_VERSION,
            # v3
            "watchdog": True, "watchdog_minutes": 5,
            "discord_webhook": "", "toast_alerts": True,
            "stop_runs": 0, "stop_hours": 0.0, "stop_on_milk": False,
            "plan": "", "plan_loop": False,
            "window_x": None, "window_y": None, "snap_to_corner": False, "corner": None,
            # v4
            "adventure": "challenges", "start_delay": 2,
            "focus_guard": True, "focus_refocus": False, "keep_awake": True,
            "on_finish": "none", "start_at": "", "stop_at": "", "resume_plan": True,
            "alert_screenshots": True, "heartbeat_minutes": 0,
            "hotkey_thru": "f6", "hotkey_hide": "f7", "hotkey_pause": "f10", "record_key": "f8",
            "dash_enabled": False, "dash_port": 8765, "dash_lan": False, "dash_token": "",
            # v5: the World / Act adventure
            "world": "nightosphere", "act": "infinite", "portal_walk": 2.0}
EVENT_PREFIX = common.EVENT_PREFIX
# settings that can change while the macro runs (sent as "SET name value")
LIVE_KEYS = ("threshold", "wait_scale", "match_timeout", "upgrade_every", "wave_every", "target_wave", "stop_runs",
             "stop_on_milk", "focus_guard", "focus_refocus", "world", "act", "record_key", "portal_walk")
DISCORD_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+/?(?:\?.*)?$")
REASON_TITLES = {"failures": "Too many failed runs", "disconnect": "Disconnected and couldn't rejoin",
                 "crashes": "Too many errors in a row", "memory": "The PC ran out of memory",
                 "templates": "Missing template images", "failsafe": "Failsafe triggered",
                 "milk": "Out of Lich Portals", "routine": "A routine stopped the macro",
                 "frozen": "The game froze"}


def load_settings():
    s = dict(DEFAULTS)
    saved = common.read_json(SETTINGS_FILE, {})
    if not isinstance(saved, dict):
        saved = {}
    s.update(saved)
    if s.get("adventure") == "bugmilk":
        # Bug Milk is now simply Nightosphere Endless under World / Act.
        s["adventure"] = "world"
        s.setdefault("world", "nightosphere")
        s.setdefault("act", "infinite")
    if int(s.get("ui_version", 1)) < UI_VERSION:
        # v1 turned click-through on by default, which made BMO unclickable while running
        s["auto_clickthrough"] = False
        s["ui_version"] = UI_VERSION
    if "adventure" not in saved:      # older settings stored the adventure as a number
        try:
            legacy = int(saved.get("mode", 0))
            s["adventure"] = {0: "challenges", 1: "world", 2: "raid", 3: "world"}.get(legacy, "challenges")
        except Exception:
            s["adventure"] = "challenges"
    return s


def send_discord(url, title, text, file=None):
    """Post to a Discord webhook, optionally with a picture attached. Returns None on success, else a short error."""
    if not DISCORD_RE.match(url):
        return "that doesn't look like a Discord webhook URL"
    payload = {"username": "BMO", "content": f"**BMO: {title}**\n{text}"[:1900],
               "allowed_mentions": {"parse": []}}
    attach = None
    if file:
        try:
            p = Path(file)
            if p.stat().st_size < 7_000_000:
                attach = (re.sub(r"[^\w.\-]", "_", p.name), p.read_bytes())
        except OSError:
            attach = None

    def body():
        if attach is None:
            return json.dumps(payload).encode("utf-8"), "application/json"
        name, data = attach
        boundary = "bmo" + secrets.token_hex(12)
        ctype = "image/png" if name.lower().endswith(".png") else "image/jpeg"
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                f'Content-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{name}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n').encode("utf-8")
        return head + data + f"\r\n--{boundary}--\r\n".encode("utf-8"), f"multipart/form-data; boundary={boundary}"

    err = "unknown error"
    for attempt in range(3):
        data, ctype = body()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": ctype,
                                              "User-Agent": "BMO-Macro-Buddy/4.0"})   # Discord rejects urllib's default
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if 200 <= r.status < 300:
                    return None
                err = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code in (401, 403, 404):
                return err + " (is the webhook deleted or the URL wrong?)"
            if attach is not None and e.code in (400, 413):
                attach = None                    # the picture was the problem: send the text alone
                continue
        except Exception as e:
            err = str(e)[:80]
        time.sleep(4)
    return err


def windows_toast(title, text):
    """Windows notification through PowerShell (no extra packages). Returns None or an error string."""
    if sys.platform != "win32":
        return "toasts are Windows-only"
    script = (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n=$t.GetElementsByTagName('text');"
        "$n.Item(0).AppendChild($t.CreateTextNode($env:BMO_TITLE)) | Out-Null;"
        "$n.Item(1).AppendChild($t.CreateTextNode($env:BMO_TEXT)) | Out-Null;"
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($t);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($toast)")
    env = os.environ.copy()
    env["BMO_TITLE"], env["BMO_TEXT"] = title[:100], text[:200]     # via env: no quoting problems
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", script], env=env, capture_output=True, text=True, timeout=25,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return None if r.returncode == 0 else (r.stderr.strip().splitlines() or ["PowerShell failed"])[0][:80]
    except Exception as e:
        return str(e)[:80]


class Win32:
    """Small, pointer-safe wrapper for the few window tweaks BMO needs."""
    GWL_EXSTYLE = -20
    LAYERED, TRANSPARENT, TOOLWINDOW, NOACTIVATE = 0x80000, 0x20, 0x80, 0x08000000

    def __init__(self):
        self.ok = False
        if sys.platform != "win32":
            return
        try:
            u = ctypes.WinDLL("user32", use_last_error=True)
            H = wintypes.HWND
            u.GetParent.argtypes = [H]
            u.GetParent.restype = H
            u.SetWindowDisplayAffinity.argtypes = [H, wintypes.DWORD]
            u.SetWindowDisplayAffinity.restype = wintypes.BOOL
            u.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.UINT]
            u.SetWindowPos.restype = wintypes.BOOL
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                self.get, self.set, long_t = u.GetWindowLongPtrW, u.SetWindowLongPtrW, ctypes.c_ssize_t
            else:
                self.get, self.set, long_t = u.GetWindowLongW, u.SetWindowLongW, ctypes.c_long
            self.get.argtypes = [H, ctypes.c_int]
            self.get.restype = long_t
            self.set.argtypes = [H, ctypes.c_int, long_t]
            self.set.restype = long_t
            self.u = u
            self.ok = True
        except Exception:
            self.ok = False

    def toplevel(self, root):
        """Tk's winfo_id() is the inner child window; styles must go on its parent."""
        root.update_idletasks()
        child = root.winfo_id()
        return self.u.GetParent(child) or child

    def set_exstyle(self, hwnd, add=0, remove=0):
        cur = int(self.get(hwnd, self.GWL_EXSTYLE))
        new = (cur | add) & ~remove
        if new != cur:
            self.set(hwnd, self.GWL_EXSTYLE, new)
        # NOMOVE | NOSIZE | NOZORDER | NOACTIVATE | FRAMECHANGED
        self.u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x2 | 0x1 | 0x4 | 0x10 | 0x20)

    def capture_exclusion(self, hwnd, on):
        return bool(self.u.SetWindowDisplayAffinity(hwnd, 0x11 if on else 0))


class BMO(ToolsMixin):
    C = C
    W, H_MAIN, H_LOG, MINI = 330, 150, 418, (176, 46)

    def __init__(self, root):
        self.root = root
        root.title("BMO Macro Buddy")
        theme.install(root)           # Adventure Time look for every pop-up window
        root.overrideredirect(True)
        root.configure(bg=C["dark"])
        root.report_callback_exception = self.on_tk_error
        self.settings = load_settings()
        self.corner = self.settings.get("corner") if self.settings.get("snap_to_corner") else None
        self.pos = self.start_position()
        self.size = (self.W, self.H_MAIN)
        root.geometry(f"{self.W}x{self.H_MAIN}+{self.pos[0]}+{self.pos[1]}")
        root.attributes("-alpha", self.settings["opacity"])
        root.attributes("-topmost", self.settings["always_on_top"])
        self.canvas = tk.Canvas(root, width=self.W, height=self.H_MAIN, bg=C["dark"],
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.win = Win32()
        self.routine_err_seen = set()
        self.routines, self.routine_errors = {}, []
        self.advs = common.build_adventures({})
        self.adv_key = str(self.settings.get("adventure") or "challenges")
        self.reload_adventures(first=True)
        self.process = None
        self.reader = None
        self.token = 0
        self.messages = queue.Queue()      # reader thread -> UI
        self.hotkeys = queue.Queue()       # pynput thread -> UI (Tk is never touched off-thread)
        self.logs = deque(maxlen=100)
        self.log_open = False
        self.minimized = False
        self.hidden = False
        self.clickthrough = False
        self.auto_ct = False
        self.blink = False
        self.eye_items = []
        self.hits = []
        self.drag = None
        self.state = "READY"
        self.activity = ""
        self.wave = None
        self.help_window = None
        self.calc = None                   # Unit Calculator window (bmo_calc.py)
        self.active_mode = None
        self.saw_events = False
        self.counts = Counter()
        self.failures = 0
        self.active_total = 0.0            # seconds the macro actually ran (not paused)
        self.seg_start = None
        self.stop_deadline = None
        self.quitting = False
        self.close_armed = 0.0
        self.closing = False
        self.listener = None
        self.last_key = {}
        self.settings_window = None
        self.var_refs = []
        self.save_job = None
        self.after_ids = {}
        self.last_sig = None
        # v3: alerts, watchdog, limits, plan, stats, log file
        self.notes = queue.Queue()         # alert/check worker threads -> UI
        self.alert_last = {}
        self.last_progress = time.monotonic()
        self.watch_hold = False            # macro is waiting for a person (recording placements)
        self.recoveries = 0
        self.best_wave = 0
        self.finish_reason = None
        self.finish_deadline = None
        self.end_reason = None             # (kind, text) from the macro's stop_reason event
        self.stop_origin = None            # "user" | "watchdog" | "quit" | "limit"
        self.active_at_start = 0.0
        self.plan_info = None
        self.checking = False
        self.report_window = None
        self.test_var = None
        self.plan_var = None
        self.last_stats_save = time.monotonic()
        self.stats_since = time.strftime("%Y-%m-%d")
        # v4
        self.focus_wait = False            # macro is holding input because Roblox lost focus
        self.focus_since, self.focus_alerted = 0.0, False
        self.pending_alert = None          # (alert, deadline): waiting for a screenshot to attach
        self.live_pending = {}             # settings changed while running -> sent as SET (debounced)
        self.plan_resume = None            # {"sig", "step", "done"}: where the chained plan stopped
        self.plan_sig = ""
        self.last_heartbeat = time.monotonic()
        self.sched_fired = {}
        self.sched_checked = 0.0
        self.hk = {}
        self.tools_init()
        self.logfile = self.open_log_file()
        self.load_stats()
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_motion)
        root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.start_hotkeys()
        self.file_log("---- BMO started ----")
        self.log("BMO online! Choose an adventure.")
        self.log(f"{self.hk_name('clickthrough')} thru | {self.hk_name('visibility')} hide/show | "
                 f"{self.record_key_name()} placement | {self.hk_name('pause')} pause")
        self.draw()
        self.later("styles", 300, self.apply_styles)
        self.later("poll", 150, self.poll)
        self.later("anim", 850, self.animate)
        self.dash_apply()

    # ------------------------------------------------------------ helpers
    def later(self, name, ms, fn):
        if self.closing:
            return
        self.after_ids[name] = self.root.after(ms, fn)

    def log(self, s):
        s = str(s).replace("\\n", " ")
        self.logs.append(s[:140])
        self.file_log(s)

    # ---- adventures: the three built in, plus one per routines/*.json ----
    def adv_label(self, key, default=""):
        for a in self.advs:
            if a["key"] == key:
                return a["label"]
        return default

    def adv_short(self, key):
        for a in self.advs:
            if a["key"] == key:
                return a["short"]
        return str(key).upper()

    def parse_plan(self, text):
        return common.parse_plan(text, self.advs)

    def reload_adventures(self, first=False):
        """(Re)read routines/*.json. Called at start-up and whenever the routine editor saves."""
        self.routines, self.routine_errors = common.load_routines(HERE)
        self.advs = common.build_adventures(self.routines)
        if not any(a["key"] == self.adv_key for a in self.advs):
            self.adv_key = "challenges"
        for fname, problem in self.routine_errors:
            if (fname, problem) not in self.routine_err_seen:
                self.routine_err_seen.add((fname, problem))
                if not first:
                    self.log(f"routines/{fname}: {problem}")
        if first:
            self.routine_err_seen.update(self.routine_errors)
        if not first:
            self.update_plan_label()
            self.last_sig = None

    def set_adventure(self, key):
        if not any(a["key"] == key for a in self.advs):
            return
        self.adv_key = key
        self.settings["adventure"] = key
        self.schedule_save()
        if self.running:
            # The macro applies this between runs, so it never abandons a match mid-way.
            if self.send_control(f"MODE {key}"):
                self.log(f"Switch to {self.adv_label(key)} queued - starts when this run ends.")
        else:
            self.active_mode, self.activity = None, ""

    def calc_open(self):
        """Open (or raise) the Unit Calculator window."""
        if bmo_calc is None:
            self.log("Unit Calculator needs bmo_calc.py and units.json beside the launcher.")
            return
        if self.calc is None:
            self.calc = bmo_calc.UnitCalculator(self.root, C, HERE, (self.pos[0] + 30, self.pos[1] + 30))
        try:
            self.calc.show()
        except Exception:
            self.calc = None
            self.on_tk_error(*sys.exc_info())

    def calc_close(self):
        c, self.calc = self.calc, None
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    def adventure_menu(self):
        """Click the adventure button: pick from a list (it used to cycle through three fixed ones)."""
        menu = tk.Menu(self.root, tearoff=0)
        for a in self.advs:
            mark = "\u2714 " if a["key"] == self.adv_key else "    "
            menu.add_command(label=mark + a["label"].title() + ("  (routine)" if a.get("routine") else ""),
                             command=lambda k=a["key"]: self.set_adventure(k))
        menu.add_separator()
        menu.add_command(label="Edit routines...", command=lambda: self.tools_menu(2))
        menu.add_command(label="Unit Calculator...", command=self.calc_open)
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    # ---- hotkey names, live settings ----
    HOTKEY_DEFAULTS = {"clickthrough": ("hotkey_thru", "f6"), "visibility": ("hotkey_hide", "f7"),
                       "pause": ("hotkey_pause", "f10")}

    def hk_name(self, action):
        setting, default = self.HOTKEY_DEFAULTS[action]
        return str(self.settings.get(setting) or default).strip().upper()

    def record_key_name(self):
        return str(self.settings.get("record_key") or "f8").strip().upper()

    @staticmethod
    def live_value(key, value):
        if key == "focus_refocus":
            return "10" if value else "0"
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    def flush_live(self):
        """Settings changed while the macro runs reach it now instead of on the next Start."""
        if not self.live_pending:
            return
        pending, self.live_pending = self.live_pending, {}
        if self.running:
            for k, v in pending.items():
                self.send_control(f"SET {k} {self.live_value(k, v)}")

    # ---- full history on disk (bmo_log.txt) ----
    def open_log_file(self):
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
                LOG_FILE.replace(LOG_FILE.with_name("bmo_log.old.txt"))
            return LOG_FILE.open("a", encoding="utf-8", buffering=1)
        except Exception:
            return None

    def file_log(self, s):
        if self.logfile is None:
            return
        try:
            self.logfile.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(s) + "\n")
        except Exception:
            self.logfile = None

    # ---- stats that survive closing BMO ----
    def load_stats(self):
        try:
            d = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            for k, v in d.get("counts", {}).items():
                self.counts[k] = int(v)
            pr = d.get("plan_resume")
            self.plan_resume = pr if isinstance(pr, dict) else None
            self.failures = int(d.get("failures", 0))
            self.active_total = float(d.get("active_total", 0.0))
            self.stats_since = str(d.get("since", self.stats_since))
        except Exception:
            pass

    def save_stats(self):
        self.last_stats_save = time.monotonic()
        try:
            common.write_json_atomic(STATS_FILE, {"counts": self.counts, "failures": self.failures,
                                                  "active_total": round(self.elapsed, 1),
                                                  "since": self.stats_since,
                                                  "plan_resume": self.plan_resume}, indent=2)
        except Exception:
            pass

    # ---- window position (remembered, optional snap to a screen corner) ----
    def work_area(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if sys.platform == "win32":
            try:
                r = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
                    return r.left, r.top, r.right, r.bottom
            except Exception:
                pass
        return 0, 0, sw, sh

    def start_position(self):
        x, y = self.settings.get("window_x"), self.settings.get("window_y")
        l, t, r, b = self.work_area()
        if isinstance(x, int) and isinstance(y, int):
            return min(max(x, l), max(l, r - 120)), min(max(y, t), max(t, b - 60))   # keep it on screen
        return 35, 45

    def corner_xy(self, w, h, m=12):
        l, t, r, b = self.work_area()
        x = l + m if self.corner[1] == "l" else r - w - m
        y = t + m if self.corner[0] == "t" else b - h - m
        return x, y

    def apply_geometry(self, w, h):
        self.size = (w, h)
        if self.corner:
            self.pos = self.corner_xy(w, h)
        self.root.geometry(f"{w}x{h}+{self.pos[0]}+{self.pos[1]}")

    def snap_to_corner(self):
        """After a drag: stick to the nearest corner of the main display's work area."""
        w, h = self.size
        l, t, r, b = self.work_area()
        cx, cy = self.pos[0] + w / 2, self.pos[1] + h / 2
        if not (l <= cx < r and t <= cy < b):
            self.corner = None                       # on another display: leave it where it is
            return
        self.corner = ("t" if cy < (t + b) / 2 else "b") + ("l" if cx < (l + r) / 2 else "r")
        self.apply_geometry(w, h)

    def on_tk_error(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        self.log(f"UI error: {val}")
        try:
            with ERROR_FILE.open("a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n") + text + "\n")
        except Exception:
            pass

    @staticmethod
    def fmt(t):
        h, r = divmod(max(0, int(t)), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    @property
    def elapsed(self):
        extra = (time.monotonic() - self.seg_start) if self.seg_start is not None else 0.0
        return self.active_total + extra

    def clock(self, on):
        """The session timer only advances while the macro is actually running."""
        now = time.monotonic()
        if on and self.seg_start is None:
            self.seg_start = now
        elif not on and self.seg_start is not None:
            self.active_total += now - self.seg_start
            self.seg_start = None

    @property
    def total_runs(self):
        return sum(self.counts.values())

    # ------------------------------------------------------------ drawing
    def rounded(self, x1, y1, x2, y2, r, fill, outline=None, width=1):
        p = [x1+r, y1, x2-r, y1, x2, y1+r, x2, y2-r, x2-r, y2, x1+r, y2, x1, y2-r, x1, y1+r]
        return self.canvas.create_polygon(p, smooth=True, splinesteps=10, fill=fill,
                                          outline=outline or fill, width=width)

    def button(self, x, y, w, h, label, fn, fill=None, size=9):
        self.rounded(x+1, y+3, x+w+1, y+h+3, 7, C["dark"])
        self.rounded(x, y, x+w, y+h, 7, C["cream"], "#FFF9E6", 2)
        self.rounded(x+3, y+3, x+w-3, y+h-3, 5, fill or C["blue"], C["white"], 1)
        self.canvas.create_line(x+9, y+6, x+w-9, y+6, fill="#B8E8EF")
        self.canvas.create_text(x+w/2, y+h/2+1, text=label, fill=C["white"],
                                font=("Comic Sans MS", size, "bold"))
        self.hits.append((x, y, x+w, y+h, fn))

    def hit_area(self, x1, y1, x2, y2, fn):
        self.hits.append((x1, y1, x2, y2, fn))

    def face(self, x, y, s=1.0):
        def r(a, b, c, d, rad, fill, out=None, w=1):
            self.rounded(x+a*s, y+b*s, x+c*s, y+d*s, rad*s, fill, out, w)
        c = self.canvas
        r(0, 0, 53, 66, 10, C["dark"], "#D8FFF1", 2)
        r(3, 3, 50, 63, 8, C["body"], "#B5F2E4")
        r(8, 9, 45, 38, 4, C["screen"], "#276F72", 2)
        self.eye_items = []
        h = 1 if self.blink else 4
        for xx in (17, 32):
            x1, y1, x2 = x+xx*s, y+17*s, x+(xx+4)*s
            self.eye_items.append((c.create_oval(x1, y1, x2, y1+h*s, fill=C["ink"], outline=""),
                                   x1, y1, x2, s))
        c.create_arc(x+22*s, y+20*s, x+32*s, y+32*s, start=200, extent=140, style="arc",
                     outline=C["ink"], width=1)
        for xx, col in ((11, "#E85A62"), (25, C["yellow"]), (38, "#B7A6E5")):
            c.create_oval(x+xx*s, y+45*s, x+(xx+8)*s, y+53*s, fill=col, outline=C["dark"])

    def status_text(self):
        if self.state == "RUNNING" and self.focus_wait:
            return "NO FOCUS"
        if self.state == "RUNNING" and self.finish_reason:
            return "FINISHING"
        return {"RUNNING": "FARMING"}.get(self.state, self.state)

    def status_color(self):
        if self.state == "RUNNING" and (self.finish_reason or self.focus_wait):
            return C["amber"]
        return {"RUNNING": C["green"], "PAUSED": C["amber"], "PAUSING": C["amber"],
                "STOPPING": C["red"], "ENDED": C["red"], "ERROR": C["red"]}.get(self.state, C["ink"])

    def draw(self):
        if self.closing:
            return
        c = self.canvas
        c.delete("all")
        self.hits = []
        if self.minimized:
            self.draw_mini()
        else:
            self.draw_full()

    def draw_mini(self):
        c, (w, h) = self.canvas, self.MINI
        c.configure(width=w, height=h)
        self.apply_geometry(w, h)
        self.rounded(1, 1, w-1, h-1, 12, C["dark"], "#D7FFF0", 2)
        self.rounded(4, 4, w-4, h-4, 10, C["body"], "#A5E9D9", 1)
        self.face(10, 8, .46)
        c.create_text(46, 16, text=self.status_text(), anchor="w", fill=C["cream"],
                      font=("Comic Sans MS", 8, "bold"))
        c.create_text(46, 31, text=(f"W{self.wave}  " if self.wave and self.running else "") +
                      f"{self.fmt(self.elapsed)}  {self.total_runs} runs", anchor="w",
                      fill="#E9FFF5", font=("Consolas", 8, "bold"))
        c.create_text(w-16, h/2, text="▢", fill=C["cream"], font=("Arial", 13, "bold"))
        self.hit_area(w-30, 4, w-2, h-4, self.restore)

    def draw_full(self):
        c = self.canvas
        h = self.H_LOG if self.log_open else self.H_MAIN
        c.configure(width=self.W, height=h)
        self.apply_geometry(self.W, h)
        self.rounded(2, 2, 328, h-2, 18, C["dark"], "#D7FFF0", 2)
        self.rounded(6, 6, 324, h-6, 15, C["body"], "#A5E9D9", 2)
        self.rounded(10, 10, 320, h-10, 12, "#71C9BE", C["dark"])
        self.face(14, 13)
        c.create_text(78, 20, text="BMO", anchor="w", fill=C["cream"], font=("Comic Sans MS", 15, "bold"))
        c.create_text(78, 38, text="MACRO BUDDY", anchor="w", fill="#E9FFF5", font=("Arial", 7, "bold"))
        c.create_text(262, 18, text="?", fill=C["cream"], font=("Comic Sans MS", 13, "bold"))
        c.create_text(284, 17, text="–", fill=C["cream"], font=("Arial", 15, "bold"))
        c.create_text(307, 18, text="×", fill=C["cream"], font=("Arial", 14, "bold"))
        self.hit_area(250, 6, 272, 32, self.help_menu)
        self.hit_area(272, 6, 296, 32, self.minimize)
        self.hit_area(296, 6, 320, 32, self.request_close)

        idle = not self.running
        self.button(76, 49, 157, 29, self.adv_label(self.adv_key)[:20], self.adventure_menu, size=8)
        self.button(239, 49, 32, 29, "⚙", self.settings_menu, size=12)
        self.button(277, 49, 39, 29, "LOG", self.toggle_log, C["blue"] if not self.log_open else C["green"], 8)
        if idle:
            self.button(76, 86, 88, 31, "▶ START", self.start, C["green"], 9)
        elif self.state in ("PAUSED", "PAUSING"):
            self.button(76, 86, 88, 31, "▶ RESUME", self.resume, C["green"], 9)
        else:
            self.button(76, 86, 88, 31, "Ⅱ PAUSE", self.pause, C["blue"], 9)
        self.button(170, 86, 67, 31, "■ STOP", self.stop, C["red"], 9)

        c.create_text(244, 94, text=self.status_text(), anchor="w", fill=self.status_color(),
                      font=("Comic Sans MS", 8, "bold"))
        c.create_text(244, 111, text="THRU ON" if self.clickthrough else f"{self.hk_name('clickthrough')} thru", anchor="w",
                      fill=C["red"] if self.clickthrough else C["ink"], font=("Arial", 7, "bold"))
        c.create_text(40, 98, text=self.fmt(self.elapsed), fill=C["ink"], font=("Consolas", 8, "bold"))
        c.create_text(40, 112, text=f"{self.total_runs} runs", fill=C["ink"], font=("Arial", 7, "bold"))
        mode = self.adv_label(self.active_mode or self.adv_key)
        doing = self.activity or ("Pick an adventure" if idle else "")
        wave = f"[Wave {self.wave}] " if self.wave and not idle else ""
        c.create_text(22, 134, text=f"{mode}: {wave}{doing}"[:46], anchor="w", fill=C["ink"], font=("Consolas", 7))

        if self.log_open:
            self.rounded(13, 148, 317, h-12, 8, C["screen"], "#D5FFF0", 2)
            c.create_text(22, 162, text="SESSION STATS", anchor="w", fill=C["ink"], font=("Comic Sans MS", 9, "bold"))
            c.create_text(310, 162, text=f"since {self.stats_since[5:]}", anchor="e", fill="#416B60", font=("Consolas", 7))
            c.create_text(22, 180, text=f"TIME  {self.fmt(self.elapsed)}", anchor="w", fill=C["ink"], font=("Consolas", 9, "bold"))
            c.create_text(166, 180, text=f"RUNS  {self.total_runs}", anchor="w", fill=C["green"], font=("Consolas", 9, "bold"))
            parts = "  ".join(f"{self.adv_short(k)[:5]} {self.counts[k]}" for k in common.BUILTIN_KEYS)
            others = sum(v for k, v in self.counts.items() if k not in common.BUILTIN_KEYS)
            if others:
                parts += f"  OTHER {others}"
            c.create_text(22, 197, text=parts, anchor="w", fill=C["ink"], font=("Consolas", 8, "bold"))
            c.create_text(22, 213, text=self.rate_text(), anchor="w", fill=C["ink"], font=("Consolas", 8, "bold"))
            c.create_text(22, 229, text=f"FAILED ATTEMPTS  {self.failures}", anchor="w", fill=C["red"], font=("Consolas", 8))
            extra = self.plan_or_limits_text()
            if extra:
                c.create_text(22, 245, text=extra[:50], anchor="w", fill=C["amber"], font=("Consolas", 8, "bold"))
            c.create_text(22, 263, text="BMO'S ADVENTURE LOG", anchor="w", fill=C["ink"], font=("Comic Sans MS", 9, "bold"))
            for i, line in enumerate(list(self.logs)[-5:]):
                c.create_text(22, 280+i*14, text=line[:49], anchor="w", fill="#416B60", font=("Consolas", 7))
            self.button(16, h-68, 96, 22, "CHECK SETUP", self.run_check, C["blue"], 7)
            self.button(118, h-68, 96, 22, "DEBUG FOLDER", self.open_debug, C["blue"], 7)
            self.button(220, h-68, 92, 22, "LOG FILE", self.open_logfile, C["blue"], 7)
            self.button(16, h-42, 78, 22, "CALC", self.calc_open, C["green"], 7)
            self.button(150, h-42, 78, 22, "RESET", self.reset_stats, C["amber"], 7)
            self.button(234, h-42, 78, 22, "TOOLS", self.tools_menu, size=7)

    def rate_text(self):
        n, t = self.total_runs, self.elapsed
        if n and t >= 120:
            avg = int(t / n)
            h, r = divmod(avg, 3600)
            m, sec = divmod(r, 60)
            avg_s = f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s"
            return f"{n / (t / 3600):.1f} runs/hr   avg {avg_s}"
        return "runs/hr and average: after 2 min"

    def plan_or_limits_text(self):
        p = self.plan_info
        if p and self.running:
            label = self.adv_label(p.get("mode"), "?")
            cnt = p.get("count") or "no limit"
            return f"PLAN {int(p.get('step', 0)) + 1}/{p.get('total')} {label.split()[0].title()} {p.get('done')}/{cnt}"
        bits = []
        if self.settings.get("stop_runs"):
            bits.append(f"{int(self.settings['stop_runs'])} runs")
        if self.settings.get("stop_hours"):
            bits.append(f"{float(self.settings['stop_hours']):g}h")
        if self.settings.get("stop_on_milk"):
            bits.append("no milk")
        if self.settings.get("plan", "").strip():
            bits.append("plan set")
        return ("STOPS AT: " + ", ".join(bits)) if bits else ""

    def animate(self):
        if self.closing:
            return
        self.blink = not self.blink
        try:
            for item, x1, y1, x2, s in self.eye_items:
                self.canvas.coords(item, x1, y1, x2, y1 + (1 if self.blink else 4) * s)
        except tk.TclError:
            pass
        self.later("anim", 850, self.animate)

    # ------------------------------------------------------------ mouse
    def hit_at(self, x, y):
        for x1, y1, x2, y2, fn in reversed(self.hits):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return fn
        return None

    def on_motion(self, e):
        self.canvas.configure(cursor="hand2" if self.hit_at(e.x, e.y) else "")

    def on_press(self, e):
        fn = self.hit_at(e.x, e.y)
        if fn:
            self.drag = None
            try:
                fn()
            except Exception as ex:
                self.on_tk_error(type(ex), ex, ex.__traceback__)
            self.draw()
        elif self.minimized or e.y <= 44:
            self.drag = (e.x_root, e.y_root, self.pos[0], self.pos[1])

    def on_drag(self, e):
        if self.drag:
            sx, sy, wx, wy = self.drag
            self.corner = None
            self.pos = (wx + e.x_root - sx, wy + e.y_root - sy)
            self.root.geometry(f"+{self.pos[0]}+{self.pos[1]}")

    def on_release(self, e):
        if self.drag:
            moved = self.pos != (self.drag[2], self.drag[3])
            self.drag = None
            if moved:
                if self.settings.get("snap_to_corner"):
                    self.snap_to_corner()
                self.remember_position()
        self.drag = None

    def remember_position(self):
        self.settings["window_x"], self.settings["window_y"] = int(self.pos[0]), int(self.pos[1])
        self.settings["corner"] = self.corner
        self.schedule_save()

    # ------------------------------------------------------------ window tweaks
    def hwnd(self):
        return self.win.toplevel(self.root)

    def apply_styles(self):
        """Top-level window styles: layered (alpha), no-activate, click-through, capture exclusion."""
        if self.closing or not self.win.ok:
            return
        try:
            hwnd = self.hwnd()
            add = Win32.LAYERED | Win32.NOACTIVATE
            if self.clickthrough:
                add |= Win32.TRANSPARENT
            self.win.set_exstyle(hwnd, add=add, remove=0 if self.clickthrough else Win32.TRANSPARENT)
            if self.settings.get("capture_exclusion", True):
                ok = self.win.capture_exclusion(hwnd, True)
                self.log("Hidden from screen capture." if ok else
                         "Capture exclusion unavailable; keep BMO off game buttons.")
            else:
                self.win.capture_exclusion(hwnd, False)
        except Exception as e:
            self.log(f"Window style error: {e}")

    def set_clickthrough(self, value):
        self.clickthrough = bool(value)
        if not value:
            self.auto_ct = False
        if self.win.ok:
            try:
                hwnd = self.hwnd()
                if value:
                    self.win.set_exstyle(hwnd, add=Win32.LAYERED | Win32.TRANSPARENT)
                else:
                    self.win.set_exstyle(hwnd, remove=Win32.TRANSPARENT)
            except Exception as e:
                self.log(f"Click-through error: {e}")
        self.log("Click-through ON - mouse goes to Roblox. F6 to undo." if value
                 else "Click-through OFF - BMO is clickable.")
        self.draw()

    def toggle_visibility(self):
        """Hide/show the overlay only; the macro keeps running either way."""
        if self.hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", self.settings["always_on_top"])
            self.hidden = False
            self.later("styles", 80, self.apply_styles)
            self.draw()
        else:
            self.root.withdraw()
            self.hidden = True

    def minimize(self):
        self.minimized = True

    def restore(self):
        self.minimized = False

    def toggle_log(self):
        self.log_open = not self.log_open

    # ------------------------------------------------------------ hotkeys (thread-safe)
    def parse_hotkey(self, name, fallback):
        name = str(name or "").strip().lower()
        try:
            if len(name) == 1:
                return keyboard.KeyCode.from_char(name)
            return keyboard.Key[name]
        except Exception:
            return keyboard.Key[fallback]

    def start_hotkeys(self):
        if keyboard is None:
            self.log("pynput unavailable: hotkeys disabled.")
            return
        ctrl_keys = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
        held = set()
        hk = self.hk = {action: self.parse_hotkey(self.settings.get(setting), default)
                        for action, (setting, default) in self.HOTKEY_DEFAULTS.items()}

        def released(k):
            held.discard(k)

        def pressed(k):
            # Runs on pynput's thread: only touch the queue, never Tk.
            try:
                if k in ctrl_keys:
                    held.add(k)
                    return
                name = None
                if k == hk["clickthrough"]:
                    name = "clickthrough"
                elif k == hk["visibility"]:
                    name = "visibility"
                elif k == hk["pause"]:
                    name = "stop" if held else "pause"      # Ctrl+key stops, key pauses/resumes
                if name:
                    now = time.monotonic()
                    if now - self.last_key.get(name, 0) > .35:
                        self.last_key[name] = now
                        self.hotkeys.put(name)
            except Exception:
                pass
        try:
            self.listener = keyboard.Listener(on_press=pressed, on_release=released, daemon=True)
            self.listener.start()
        except Exception as e:
            self.log(f"Hotkey listener error: {e}")

    def restart_hotkeys(self):
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
        self.start_hotkeys()
        self.log(f"Hotkeys: {self.hk_name('clickthrough')} thru | {self.hk_name('visibility')} hide/show | "
                 f"{self.hk_name('pause')} pause")

    def handle_hotkeys(self):
        changed = False
        while True:
            try:
                name = self.hotkeys.get_nowait()
            except queue.Empty:
                return changed
            if name == "clickthrough":
                self.set_clickthrough(not self.clickthrough)
            elif name == "visibility":
                self.toggle_visibility()
            elif name == "pause":
                if self.state in ("PAUSED", "PAUSING"):
                    self.resume()
                else:
                    self.pause()
            elif name == "stop":
                self.stop()
            changed = True

    # ------------------------------------------------------------ settings
    def schedule_save(self):
        if self.save_job is None and not self.closing:
            self.save_job = self.root.after(600, self.save)

    def save(self):
        self.save_job = None
        self.settings["adventure"] = self.adv_key
        try:
            common.write_json_atomic(SETTINGS_FILE, self.settings, indent=2)
        except Exception as e:
            self.log(f"Settings save failed: {e}")

    def set_setting(self, k, v):
        self.settings[k] = v
        if k == "opacity":
            self.root.attributes("-alpha", v)
        elif k == "always_on_top":
            self.root.attributes("-topmost", bool(v))
        elif k == "capture_exclusion":
            self.apply_styles()
        elif k == "snap_to_corner":
            if v:
                self.snap_to_corner()
                self.remember_position()
                self.draw()
            else:
                self.corner = None
                self.settings["corner"] = None
        elif k in ("hotkey_thru", "hotkey_hide", "hotkey_pause"):
            old = self.after_ids.get("hk")
            if old:
                try:
                    self.root.after_cancel(old)
                except Exception:
                    pass
            self.later("hk", 900, self.restart_hotkeys)        # wait until typing stops
        if k in LIVE_KEYS:
            self.live_pending[k] = v
        self.schedule_save()

    def settings_menu(self):
        if self.clickthrough:
            self.set_clickthrough(False)
        if self.settings_window is not None:
            try:
                if self.settings_window.winfo_exists():
                    self.settings_window.deiconify()
                    self.settings_window.lift()
                    return
            except tk.TclError:
                pass
            self.settings_window = None
        w = tk.Toplevel(self.root)
        self.settings_window = w
        w.title("BMO's Settings")
        x, y = self.pos[0] + 20, self.pos[1] + 20
        w.geometry(f"500x700+{x}+{y}")
        w.minsize(440, 520)
        w.resizable(False, True)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close_settings)
        body = theme.frame_window(w, "BMO'S SETTINGS")
        foot = tk.Frame(body, bg=theme.T["paper"])
        foot.pack(side="bottom", fill="x", pady=(6, 8))          # packed first so the tabs can't push it off
        theme.Pill(foot, "SAVE", self.save, "green", width=104).pack(side="left", padx=16)
        tk.Label(foot, text=theme.quote(), bg=theme.T["paper"], fg=theme.T["star"],
                 font=(theme.FONT, 10, "bold")).pack(side="left", expand=True)
        theme.Pill(foot, "CLOSE", self.close_settings, "cream", width=90).pack(side="right", padx=16)
        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        def tab(title):
            f = theme.ScrollFrame(nb, bg=theme.T["paper"])
            nb.add(f, text=title)
            return f.inner

        def head(parent, text):
            theme.heading(parent, text, wrap=390)

        t = tab("Overlay")
        head(t, "OVERLAY")
        self.check(t, "Keep BMO above other windows", "always_on_top")
        self.check(t, "Hide BMO from Windows screen capture", "capture_exclusion")
        self.check(t, "Auto click-through when macro starts (the click-through key undoes it)", "auto_clickthrough")
        self.check(t, "Snap BMO to the nearest screen corner when dragged", "snap_to_corner")
        self.scale(t, "Overlay opacity", "opacity", .55, 1, .05)
        head(t, "HOTKEYS - a key name like f6, pause, insert, or one letter (avoid F9 / F11, and use different keys)")
        self.entry(t, "Click-through on/off", "hotkey_thru")
        self.entry(t, "Hide / show BMO", "hotkey_hide")
        self.entry(t, "Pause / resume  (Ctrl + this key stops the macro)", "hotkey_pause")
        self.entry(t, "Finish recording unit placement (default f8; applies while the macro runs)", "record_key")

        t = tab("Macro")
        head(t, "MACRO SETTINGS - THESE APPLY WHILE IT RUNS")
        self.scale(t, "Image match threshold", "threshold", .65, .95, .01)
        self.scale(t, "Wait time multiplier (lower = faster, 1.0 = the old speed; too low can miss pop-ups)", "wait_scale", .3, 1.5, .05)
        self.scale(t, "Button match timeout (seconds)", "match_timeout", 5, 45, 1)
        self.scale(t, "Upgrade All interval (seconds)", "upgrade_every", .5, 5, .1)
        self.scale(t, "Start/Skip Wave interval (seconds)", "wave_every", .5, 4, .1)
        self.scale(t, "Portal walk (seconds, the most BMO walks forward to reach the portal; lower it if lag makes it overshoot)", "portal_walk", 1.0, 3.5, .1)
        self.scale(t, "Endless target wave (0 = play until defeated)", "target_wave", 0, 50, 1)
        self.scale(t, "Countdown after Start, seconds (applies on next Start)", "start_delay", 0, 10, 1)
        head(t, "WORLD / ACT ADVENTURE - pick the world and stage it plays (applies on the next run)")
        self.choice(t, "world", [("candy", "Candy Kingdom"), ("ice", "Ice Kingdom"), ("fire", "Fire Kingdom"),
                                 ("lemon", "Castle Lemongrab"), ("crystal", "Crystal Dimension"),
                                 ("factory", "Mo Factory"), ("nightosphere", "Nightosphere"),
                                 ("lumpy", "Lumpy World"), ("wizard", "Wizard World")])
        self.choice(t, "act", [("infinite", "Infinite"), ("1", "Act 1"), ("2", "Act 2"), ("3", "Act 3"),
                               ("4", "Act 4"), ("5", "Act 5"),
                               ("wish", "Wish Run (any world)")])

        t = tab("Stops")
        head(t, "STOP CONDITIONS - the macro finishes its current run first")
        self.scale(t, "Stop after this many runs (0 = never; applies now)", "stop_runs", 0, 500, 1)
        self.scale(t, "Stop after this many hours of running (0 = never; applies now)", "stop_hours", 0, 24, .5)
        self.check(t, "Stop when the Lich raid has no portals left (instead of carrying on with the plan / challenges)", "stop_on_milk")
        head(t, "CHAINED PLAN (empty = just run the adventure you picked)")
        self.entry(t, "Steps, e.g.  raid:5, challenges:3, world:4  (routine names work too)", "plan", plan=True)
        self.plan_var = tk.StringVar(master=t)
        self.var_refs.append(self.plan_var)
        plan_lb = tk.Label(t, textvariable=self.plan_var, bg=theme.T["paper"], fg=theme.T["brown"],
                           font=(theme.FONT, 8), wraplength=360, justify="left")
        plan_lb.pack(anchor="w", padx=12)
        theme.autowrap(plan_lb, 40)
        self.update_plan_label()
        self.check(t, "Start the plan over when it ends", "plan_loop")
        self.check(t, "After a stop, continue the plan where it left off (same plan only)", "resume_plan")

        t = tab("Safety")
        head(t, "WHILE THE MACRO RUNS")
        self.check(t, "Hold all clicks and keys when Roblox isn't the active window (they would land in "
                      "whatever you switched to)", "focus_guard")
        self.check(t, "...and bring Roblox back to the front after 10 seconds", "focus_refocus")
        self.check(t, "Keep the PC awake (applies on next Start)", "keep_awake")
        head(t, "WHEN IT FINISHES CLEANLY (a limit, the plan, or out of Lich Portals)")
        self.choice(t, "on_finish", [("none", "Do nothing"), ("close_roblox", "Close Roblox"),
                                     ("sleep", "Put the PC to sleep"),
                                     ("shutdown", "Shut the PC down (90 second warning; cancel with: shutdown /a)")])
        head(t, "SCHEDULE - 24-hour HH:MM, empty = off")
        self.entry(t, "Start the macro at (BMO must be open)", "start_at")
        self.entry(t, "Finish the current run and stop at", "stop_at")

        t = tab("Alerts")
        head(t, "STUCK WATCHDOG")
        self.check(t, "Watch for a stuck macro and try to recover", "watchdog")
        self.scale(t, "Counts as stuck after this many minutes with no change", "watchdog_minutes", 2, 20, 1)
        head(t, "ALERTS (stuck, macro ended, failures, disconnects, finished)")
        self.check(t, "Windows notification", "toast_alerts")
        self.check(t, "Attach a screenshot of the game to Discord alerts", "alert_screenshots")
        self.entry(t, "Discord webhook URL (alerts reach your phone through Discord)", "discord_webhook", show="\u2022")
        self.scale(t, "'Still running' message every N minutes (0 = off)", "heartbeat_minutes", 0, 180, 5)
        row = tk.Frame(t, bg=theme.T["paper"])
        row.pack(fill="x", padx=12, pady=(8, 0))
        theme.Pill(row, "SEND TEST ALERT", self.test_alert, "blue", width=150, height=28, size=8).pack(side="left")
        self.test_var = tk.StringVar(master=t)
        self.var_refs.append(self.test_var)
        test_lb = tk.Label(row, textvariable=self.test_var, bg=theme.T["paper"], fg=theme.T["brown"],
                           font=(theme.FONT, 8), wraplength=200, justify="left")
        test_lb.pack(side="left", padx=8)
        theme.autowrap(test_lb, 150 + 16 + 24 + 16)
        note_lb = tk.Label(t, text="The webhook URL is stored in bmo_settings.json - don't share that file.",
                           bg=theme.T["paper"], fg=C["amber"], font=(theme.FONT, 8), wraplength=360,
                           justify="left")
        note_lb.pack(anchor="w", padx=12, pady=(8, 10))
        theme.autowrap(note_lb, 40)

    def check(self, parent, label, key, refs=None, after=None):
        v = tk.BooleanVar(master=parent, value=bool(self.settings.get(key)))
        (self.var_refs if refs is None else refs).append(v)     # keep alive on the main thread (see module docstring)

        def changed():
            self.set_setting(key, v.get())
            if after is not None:
                after()
        theme.toggle_row(parent, label, v, changed)

    def choice(self, parent, key, options, refs=None):
        v = tk.StringVar(master=parent, value=str(self.settings.get(key) or options[0][0]))
        (self.var_refs if refs is None else refs).append(v)
        theme.radio_group(parent, v, options, lambda: self.set_setting(key, v.get()))

    INT_SETTINGS = ("target_wave", "match_timeout", "stop_runs", "watchdog_minutes", "start_delay",
                    "heartbeat_minutes")

    def scale(self, parent, label, key, lo, hi, step):
        frame = tk.Frame(parent, bg=theme.T["paper"])
        frame.pack(fill="x", padx=10, pady=(4, 0))
        lb = tk.Label(frame, text=label, bg=theme.T["paper"], fg=theme.T["brown"], font=(theme.FONT, 9),
                      wraplength=360, justify="left")
        lb.pack(anchor="w", padx=2)
        theme.autowrap(lb, 40)
        var = tk.DoubleVar(master=parent, value=self.settings[key])
        self.var_refs.append(var)
        sl = theme.Slider(frame, var, lo, hi, step, bg=theme.T["paper"], width=360,
                          command=lambda val, k=key: self.set_setting(
                              k, int(float(val)) if k in self.INT_SETTINGS else float(val)))
        sl.pack(anchor="w")
        theme.autosize(sl, 40)

    def entry(self, parent, label, key, show=None, plan=False):
        lb = tk.Label(parent, text=label, bg=theme.T["paper"], fg=theme.T["brown"], font=(theme.FONT, 9),
                      wraplength=360, justify="left")
        lb.pack(anchor="w", padx=12, pady=(6, 0))
        theme.autowrap(lb, 40)
        var = tk.StringVar(master=parent, value=str(self.settings.get(key) or ""))
        self.var_refs.append(var)

        def changed(*_):
            self.set_setting(key, var.get().strip())
            if plan:
                self.update_plan_label()
        var.trace_add("write", changed)
        tk.Entry(parent, textvariable=var, show=show or "", width=46, font=("Consolas", 9)).pack(
            anchor="w", padx=12, pady=3, ipady=2)

    def update_plan_label(self):
        if self.plan_var is None:
            return
        steps, bad = self.parse_plan(self.settings.get("plan", ""))
        try:
            if bad:
                self.plan_var.set("Problem: " + "; ".join(bad))
            elif steps:
                self.plan_var.set("OK: " + common.plan_text(steps, self.advs))
            else:
                self.plan_var.set("No plan - BMO runs the adventure on its button.")
        except tk.TclError:
            pass

    HELP = [
        ("QUICK START", [
            ("1. Set up", "Windows 10/11, 64-bit Python 3.10+. Run install.bat once, then start_bmo.bat."),
            ("2. Roblox", "Windowed or fullscreen on your MAIN display at 1280x720 or 1920x1080. Windows display scale 100%. Default Roblox graphics/UI settings. Don't move or resize the window during a run."),
            ("3. Check", "Click LOG > CHECK SETUP (with Roblox open) and fix anything it flags. It also locks in your resolution so every Start is faster. Run it again whenever you change resolution."),
            ("4. Pick", "Click the adventure button and choose what to farm."),
            ("5. Start", "Press START, then click into Roblox. It begins after the countdown (Settings > Macro, default 2 seconds)."),
            ("6. New map?", "Place your units once yourself and press the record key (F8). BMO replays them every run after that. If you walk (W/A/S/D) between placements, that is recorded too."),
            ("7. Walk away", "Watch the first run. Set up alerts (Settings > Alerts) and stops (Settings > Stops) before leaving it."),
        ]),
        ("BUTTONS", [
            ("Adventure button", "Click for a list: Dungeon Train, World / Act / Endless, Lich Raid, Unit Calculator, plus any routines you made. While running, a switch is queued and starts when the current run ends."),
            ("⚙ (Settings)", "Overlay + hotkeys, macro tuning (applies live), world/act, stops + plan, safety, watchdog and alerts. Every change saves as you make it."),
            ("TOOLS (in LOG)", "History, templates tester + capture, routine editor, placements, profiles, remote web page."),
            ("CALC (in LOG)", "Unit Calculator: pick up to 6 units with trait, level and roll grades to see their stats and team DPS. Also in the adventure button's list. Close it before the macro starts clicking."),
            ("LOG", "Show or hide session stats and the adventure log."),
            ("▶ START", "Starts the chosen adventure. Click into Roblox: it begins after the countdown (Settings > Macro, default 2 seconds)."),
            ("Ⅱ PAUSE / ▶ RESUME", "Freezes the macro after its current click. The session timer stops while paused."),
            ("■ STOP", "Stops the macro only; BMO stays open. Click again to force-stop."),
            ("?", "This help window."),
            ("–", "Shrinks BMO to a small pill. The macro keeps running. Click ▢ to restore."),
            ("×", "Quits BMO. If a macro is running, click twice (it stops the macro first)."),
        ]),
        ("HOTKEYS (defaults - change them in Settings > Overlay)", [
            ("F6", "Click-through on/off. Mouse clicks go to Roblox and THRU ON shows on BMO."),
            ("F7", "Hide / show BMO. The macro keeps running while BMO is hidden."),
            ("{record_key}", "Finish recording unit placement on a new map (default F8; change it in Settings > Overlay)."),
            ("F10", "Pause / resume the macro, even with click-through on."),
            ("Ctrl+F10", "Stop the macro (press again within 8 seconds to force-stop)."),
            ("Mouse to top-left", "Emergency stop (macro failsafe)."),
            ("Choosing keys", "Use a key name (f6, pause, insert) or one letter. Avoid F9 and F11, and give every action a different key."),
        ]),
        ("STATUS WORDS", [
            ("READY / STOPPED / ENDED", "Not running. ENDED means the macro quit on its own with a problem (too many failures, disconnected, crashed)."),
            ("STARTING", "Macro launched, waiting out its countdown."),
            ("NO FOCUS", "Roblox isn't the active window, so the macro is holding all clicks and keys. Click back into Roblox (or tick the auto-refocus option)."),
            ("FARMING", "Running."),
            ("FINISHING", "A stop condition was reached; the macro stops after the current run."),
            ("PAUSING / PAUSED", "Pause requested / confirmed."),
            ("STOPPING", "Waiting for the macro to exit (force-killed after 8 seconds)."),
        ]),
        ("BMO'S BOTTOM LINE", [
            ("Adventure: [Wave N] ...", "The adventure, the wave read from Roblox's wave counter, and what the macro is doing (placing units, starting wave 1, going home...)."),
        ]),
        ("ADVENTURES", [
            ("DUNGEON TRAIN", "Takes a challenge car, places units, plays, returns to the hub."),
            ("WORLD / ACT / ENDLESS", "Choose a world and either Act 1-5 or Infinite (Settings > Macro). Goes Portal > world > stage > Play > Start. Numbered acts use Play Again between runs and Return to Hub when a limit is reached."),
            ("Endless target wave", "0 = keep playing until defeated, then start another run. A number = go home at that wave."),
            ("WISH RUN", "Pick Act > 'Wish Run (any world)'. Plays like Infinite; every 5 waves it picks the best wish card: Legendary (gold) over Rare (purple) over Common (blue), random between equals."),
            ("LICH RAID", "Opens a Lich Portal from your items and fights on the Lich map. Press B (or the items button) > Your Items > Lich Portal icon (scrolls the list if needed) > Open Lich Portal > Enter. After each battle: Open Lich > Next > Yes, then it re-places your units and goes again. When the end screen shows none left, it presses Back, goes home, then carries on with your plan (or Dungeon Train if nothing is planned)."),
            ("Already in a match?", "Press START and BMO joins it instead of walking back to the hub."),
            ("Unit placements", "Each world/act has its own saved placements. Record once with the record key; they replay every run."),
            ("Walking in placements", "W/A/S/D are recorded with how long each was held (W+D together stays diagonal) and replayed the same way. If the character walks into a wall, BMO lets go of the key early. A small picture is saved after each walk (<map>_view1.png) so later spots follow where you ended up. Re-record a map to add its walking. BMO_WALL_STOP=0 turns wall detection off."),
            ("Extra button pictures", "If a button is sometimes bigger (hovered, selected, or an act end screen with no Next), save another picture of it: play_again_2.png, awesome_1.png, return_hub_2.png. Any picture also accepts _1 to _5 copies. BMO tries them all."),
        ]),
        ("SETTINGS > OVERLAY", [
            ("Keep BMO above other windows", "Stops Roblox covering the overlay."),
            ("Hide from screen capture", "BMO won't appear in screenshots the macro takes, so it can't confuse image matching."),
            ("Auto click-through", "Turns click-through on when the macro starts (the click-through key undoes it)."),
            ("Snap to corner", "BMO jumps to the nearest screen corner when you drag it."),
            ("Overlay opacity", "How see-through BMO is."),
            ("Hotkeys", "Click-through, hide/show, pause (Ctrl + pause key stops), and the record key. The record key applies while the macro runs."),
        ]),
        ("SETTINGS > MACRO (all apply live except where noted)", [
            ("Image match threshold", "How closely a picture must match the screen to count as found. Higher = stricter. Lower it if buttons are missed; raise it if it clicks wrong things."),
            ("Wait time multiplier", "Scales every built-in wait. Lower = faster, 1.0 = the original speed. Too low can miss pop-ups."),
            ("Button match timeout", "Seconds to keep looking for a button before giving up on that step."),
            ("Upgrade All interval", "Seconds between Upgrade All presses during a match."),
            ("Start/Skip Wave interval", "Seconds between Start/Skip Wave presses."),
            ("Endless target wave", "0 = play until defeated, otherwise go home at that wave."),
            ("Countdown after Start", "Seconds between pressing START and the first click (default 2). Applies on the next Start. 0 = begin immediately."),
            ("World / Act", "Which world (Candy, Ice, Fire, Lemon, Crystal, Factory, Nightosphere, Lumpy, Wizard) and which stage (Infinite, Act 1-5, or Wish Run). Applies on the next run."),
        ]),
        ("SETTINGS > STOPS & PLANS", [
            ("Stop after N runs / hours", "The macro finishes the run it is in, then stops and sends an alert. 0 = never. Applies now."),
            ("Stop when out of Lich Portals", "The raid stops the macro instead of carrying on with the plan / challenges."),
            ("Plan", "Chain adventures, e.g. raid:5, challenges:3, world:4. Routine names work too. A step without a count (last one only) runs until you stop it. Running out of Lich Portals skips the raid step. The line under the box says OK or what's wrong."),
            ("Start the plan over", "Loops back to step 1 when the plan ends."),
            ("Continue where it left off", "After a stop, resumes the same plan at the step it reached."),
        ]),
        ("SETTINGS > SAFETY", [
            ("Focus guard", "While running, every click and key press is held unless Roblox is the active window. Alt-tab away and it waits instead of typing into your other program."),
            ("Bring Roblox back", "Optionally returns Roblox to the front after 10 seconds away."),
            ("Keep awake", "Stops Windows sleeping or blanking the screen while running (a blank screen looks like a frozen game). Applies on next Start."),
            ("When it finishes", "After a clean finish (limit reached, plan done, out of Lich Portals): do nothing, close Roblox, sleep, or shut down (90 second warning; cancel with  shutdown /a)."),
            ("Schedule", "24-hour HH:MM. Start the macro at a time, or finish the current run and stop at a time. BMO must be open. Empty = off."),
            ("If BMO dies", "The macro notices its control pipe closed and stops itself, so it can't keep clicking unattended."),
            ("Frozen game", "If the match screen doesn't change at all for a couple of minutes, the macro treats the game as hung and recovers."),
        ]),
        ("SETTINGS > ALERTS & WATCHDOG", [
            ("Stuck watchdog", "If the activity, wave and run count don't change for the set minutes, BMO tells the macro to recover (screenshot to debug/, rejoin or return). After 3 failed recoveries it stops the macro. It sleeps while paused and while waiting for you to record placements."),
            ("Windows notification", "A pop-up for each alert. Windows may hide these under Focus Assist / Do Not Disturb."),
            ("Discord webhook", "Paste a webhook URL (Discord channel settings > Integrations > Webhooks) to get alerts on your phone via the Discord app. It's stored in bmo_settings.json: never share that file."),
            ("Attach screenshot", "Adds a picture of the game to Discord alerts."),
            ("'Still running' message", "A heartbeat every N minutes. 0 = off."),
            ("What triggers an alert", "Stuck, too many failures, disconnected and couldn't rejoin, macro crashed or ended, and finished. Stopping it yourself sends nothing."),
            ("SEND TEST ALERT", "Shows exactly what failed if nothing arrives."),
        ]),
        ("LOG PANEL & SESSION STATS", [
            ("TIME / RUNS", "Time only counts while the macro is running. Stats are saved and survive closing BMO; RESET clears them."),
            ("runs/hr, avg", "Completed runs per hour and the average time per run."),
            ("FAILED ATTEMPTS", "Runs that failed and were retried."),
            ("PLAN / STOPS AT", "Where the chained plan is, or which stop conditions are set."),
            ("CHECK SETUP", "Checks pictures (templates), saved placements, and that Roblox is on the display the macro captures. Reports your resolution as Supported or Unsupported and LOCKS IT IN: the next Starts use that resolution without re-detecting it. Run it before walking away, and again after changing resolution."),
            ("DEBUG FOLDER", "Opens the folder of failure screenshots the macro saves."),
            ("LOG FILE", "Opens bmo_log.txt, the full history of what happened. It rolls over to bmo_log.old.txt at about 2 MB."),
        ]),
        ("TOOLS (LOG > TOOLS)", [
            ("History", "Every completed run and failed attempt with time, duration and wave; runs-per-hour chart; CSV export."),
            ("Templates", "Scores every picture the macro looks for against your screen right now. 'Capture' lets you drag a box on a screenshot to save a new picture. Give it the SAME name as the original to replace it at your resolution."),
            ("Routines", "Build adventures as a list of steps. Test any step, validate, then pick the routine from the adventure button. See ROUTINE STEPS below."),
            ("Placements", "See saved unit placements; 're-record' moves one aside so the next run asks you to place again."),
            ("Profiles", "Save and load groups of settings (window position, webhook and remote token are not included)."),
            ("Remote", "A password-protected web page to watch (with screenshots) and pause / stop / start BMO from your phone. Off by default. Options: turn on, allow other devices on your network, port, new token. Use Tailscale, never port-forward."),
        ]),
        ("ROUTINE STEPS (TOOLS > Routines)", [
            ("click_template", "Wait for a picture, then click it. Can scroll (mouse wheel) or page (arrow picture) while looking; dx/dy shift the click point."),
            ("wait_template", "Wait until a picture appears, or with 'gone' until it disappears."),
            ("press / hold_key", "Tap a key / hold a key until a picture shows up (e.g. walk until a menu pops up)."),
            ("click_at / scroll", "Click a fixed spot (prefer click_template: fixed spots break when the UI moves) / turn the mouse wheel."),
            ("sleep", "Wait a number of seconds."),
            ("if_template / if_result", "Branch on a picture being on screen / on how the last play_match ended (victory, ended, done, failed)."),
            ("repeat / break", "Run steps N times or forever; break leaves the innermost repeat."),
            ("play_match", "Place units (if this map has placements), then play until the match ends. Kinds: challenge, infinite, raid, wish."),
            ("area / go_home / set_map", "Open the Areas menu and press Go on a card / go home using whichever of Return to Hub, Lobby (either look) is on screen / say which map this is."),
            ("dismiss_daily / rejoin", "Close the Daily Rewards window / get back into the game."),
            ("log / activity", "Write a line in BMO's log / change the activity text on BMO."),
            ("run / count_run", "Run another routine as a sub-routine / count a completed run."),
            ("finish / stop", "Stop cleanly after this run / stop now and send an alert."),
            ("Every step", "Also accepts label (text shown while it runs), optional (carry on if it fails) and retries (extra tries, 1 second apart)."),
        ]),
        ("UNIT CALCULATOR (CALC)", [
            ("Team", "Up to 6 units, each with trait, level (1-50), roll grades for damage / range / speed, and how many are placed. Saved to bmo_calc_team.json after every change."),
            ("Maths", "stat = base x (1 + roll bonus) x (1 + trait bonus). Damage also x (1 + 0.04 x (level - 1)). Speed bonuses DIVIDE SPA (attack delay), so more speed = lower SPA."),
            ("DPS", "DPS each = max-upgrade damage / max-upgrade SPA. DPS (all placed) = DPS each x units placed."),
            ("Grades", "A grade box also accepts a plain number, e.g. 0.35 for +35%."),
            ("Other tabs", "Unit list (search and sort every unit) and Grades + traits (the grade and trait tables)."),
        ]),
        ("RESOLUTION & PICTURES", [
            ("Supported sizes", "1280x720 and 1920x1080. Other 16:9 sizes often work but aren't tested."),
            ("Picture sizes", "Learned on the first run and saved in scale_hints.json. Delete that file to relearn."),
            ("Resolution lock", "CHECK SETUP saves your resolution (resolution_profile.json). On Start the macro trusts it: no re-measuring, the right pictures folder is used straight away, all pictures are loaded during the countdown, and a picture that matches at its remembered size is accepted without trying other sizes. If your Roblox window no longer matches the saved size, the log says so and BMO goes back to detecting it by itself until you run CHECK SETUP again."),
            ("Per-resolution pictures", "If a fixed-size picture (E prompts, Play buttons) doesn't match, capture it at your resolution under the same name; it's saved in templates/<width>x<height>/ and used automatically."),
            ("Changed resolution?", "Re-record your unit placements."),
        ]),
        ("OPTIONAL AI HELPER", [
            ("What it does", "If the macro is stuck for two minutes trying to get back into the game, BMO can send one small picture of Roblox to a Claude vision model and log what screen it thinks it is. It only reports; it never clicks."),
            ("Turning it on", "Off by default. Needs your own API key in ai_assist.json beside macro.py (see the top of bmo_assist.py). Never share that file."),
        ]),
        ("IF SOMETHING GOES WRONG", [
            ("BMO ignores clicks", "Press F6 (click-through is probably on)."),
            ("BMO disappeared", "Press F7. It may just be hidden."),
            ("Macro can't find buttons", "Run CHECK SETUP. Roblox must be windowed at 1280x720 (or 1920x1080) on the display the macro is using, with display scale 100%. Try lowering the match threshold."),
            ("Clicks go to the wrong window", "Leave Focus guard on, and keep Roblox in front."),
            ("Stuck on one screen", "Check LOG FILE and the newest picture in the DEBUG FOLDER. The watchdog will try to recover on its own."),
            ("Unit placement wrong", "TOOLS > Placements > re-record that map, then place and press the record key again."),
            ("Play screen didn't show at the portal", "Probably lag. BMO walks forward to get the main screen back, reopens Areas, presses the Portal and walks in again (3 tries)."),
            ("Went home instead of Play Again", "Save a picture of the Play Again button as templates/play_again_2.png (and a hovered Return to Hub as return_hub_2.png)."),
            ("No alerts arrive", "Settings > Alerts > SEND TEST ALERT shows what failed. Windows may hide notifications under Focus Assist / Do Not Disturb."),
            ("A saved file got damaged", "BMO keeps it as <name>.bad (nothing is deleted) and starts that one fresh."),
            ("BMO errors", "Saved to bmo_error.log next to the launcher."),
            ("Still stuck?", "Send the log file plus the newest picture in the debug folder."),
        ]),
        ("USE AT YOUR OWN RISK", [
            ("Heads up", "Automation may be against Roblox's or the game's rules and could get an account actioned. Use an alt, not your main."),
        ]),
    ]

    def help_menu(self):
        if self.clickthrough:
            self.set_clickthrough(False)
        if self.help_window is not None:
            try:
                if self.help_window.winfo_exists():
                    self.help_window.deiconify()
                    self.help_window.lift()
                    return
            except tk.TclError:
                pass
            self.help_window = None
        w = tk.Toplevel(self.root)
        self.help_window = w
        w.title("BMO Help")
        x, y = self.root.winfo_x() + 30, self.root.winfo_y() + 30
        w.geometry(f"660x600+{x}+{y}")
        w.minsize(480, 360)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close_help)
        body = theme.frame_window(w, "BMO'S HELP")
        foot = tk.Frame(body, bg=theme.T["paper"])
        foot.pack(side="bottom", fill="x", pady=(4, 8))
        tk.Label(foot, text=theme.quote(), bg=theme.T["paper"], fg=theme.T["star"],
                 font=(theme.FONT, 10, "bold")).pack(side="left", padx=16)
        theme.Pill(foot, "CLOSE", self.close_help, "cream", width=90).pack(side="right", padx=16)
        frame = tk.Frame(body, bg=theme.T["paper"])
        frame.pack(fill="both", expand=True, padx=10, pady=(2, 4))
        bar = ttk.Scrollbar(frame, orient="vertical")
        bar.pack(side="right", fill="y")
        text = tk.Text(frame, wrap="word", bg=theme.T["paper_lt"], fg=theme.T["brown"], font=("Consolas", 9),
                       relief="flat", yscrollcommand=bar.set, padx=12, pady=6, cursor="arrow",
                       highlightthickness=2, highlightbackground=theme.T["edge"])
        text.pack(side="left", fill="both", expand=True)
        bar.config(command=text.yview)
        text.tag_configure("h", font=(theme.FONT, 10, "bold"), foreground=theme.T["title"], spacing1=12, spacing3=3)
        text.tag_configure("star", font=("Arial", 10), foreground=theme.T["star"])
        text.tag_configure("row", tabs=("200",), lmargin1=6, lmargin2=200, spacing3=2)
        text.tag_configure("key", font=("Consolas", 9, "bold"), foreground=theme.T["slate_dk"])
        for title, rows in self.HELP:
            text.insert("end", "\u2605 ", ("h", "star"))
            text.insert("end", title + "\n", "h")
            for key, desc in rows:
                if key == "{record_key}":          # the key can be changed in Settings, so it is filled in here
                    key = self.record_key_name()
                text.insert("end", key, ("row", "key"))
                text.insert("end", "\t" + desc + "\n", "row")
        text.configure(state="disabled")

    def close_help(self):
        w, self.help_window = self.help_window, None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass

    def close_settings(self):
        w, self.settings_window = self.settings_window, None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self.test_var = self.plan_var = None
        self.var_refs.clear()
        gc.collect()                # free the Tk variables here, on the Tk thread
        self.save()

    # ------------------------------------------------------------ modes + macro process
    def reset_stats(self):
        self.counts = Counter()
        self.failures = 0
        self.active_total = 0.0
        self.active_at_start = 0.0
        self.stats_since = time.strftime("%Y-%m-%d")
        self.seg_start = time.monotonic() if self.running and self.state not in ("PAUSED",) else None
        self.save_stats()
        self.log("Session stats reset.")

    def python_exe(self):
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
            exe = exe.parent / "python.exe"
        return exe

    def macro_env(self):
        env = os.environ.copy()
        for key, envkey in (("threshold", "CANDY_THRESHOLD"), ("match_timeout", "CANDY_MATCH_TIMEOUT"),
                            ("upgrade_every", "CANDY_UPGRADE_EVERY"), ("wave_every", "CANDY_WAVE_EVERY"),
                            ("target_wave", "CANDY_TARGET_WAVE")):
            env[envkey] = str(self.settings[key])
        env["CANDY_WAIT_SCALE"] = str(self.settings.get("wait_scale", 0.8))
        env["CANDY_WORLD"] = str(self.settings.get("world") or "nightosphere")
        env["CANDY_ACT"] = str(self.settings.get("act") or "infinite")
        env["CANDY_PORTAL_WALK"] = str(self.settings.get("portal_walk", 2.0))
        env["CANDY_RECORD_KEY"] = str(self.settings.get("record_key") or "f8")
        env["CANDY_STOP_RUNS"] = str(int(self.settings.get("stop_runs") or 0))
        env["CANDY_STOP_ON_MILK"] = "1" if self.settings.get("stop_on_milk") else "0"
        env["CANDY_PLAN_LOOP"] = "1" if self.settings.get("plan_loop") else "0"
        env["CANDY_START_DELAY"] = str(self.settings.get("start_delay", 2))
        env["CANDY_FOCUS_GUARD"] = "1" if self.settings.get("focus_guard", True) else "0"
        env["CANDY_FOCUS_REFOCUS"] = "10" if self.settings.get("focus_refocus") else "0"
        env["CANDY_KEEP_AWAKE"] = "1" if self.settings.get("keep_awake", True) else "0"
        env["CANDY_UNDER_BMO"] = "1"        # the macro stops itself if BMO (its control pipe) disappears
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def resume_point(self, steps):
        """Where to pick the plan up again, if the last session of this same plan was stopped part-way."""
        pr = self.plan_resume
        if not (steps and pr and self.settings.get("resume_plan", True) and pr.get("sig") == self.plan_sig):
            return None
        try:
            step, done = int(pr.get("step", 0)), int(pr.get("done", 0))
        except (TypeError, ValueError):
            return None
        if 0 <= step < len(steps) and (step, done) != (0, 0):
            return step, done
        return None

    def start(self):
        if self.running:
            return
        if not MACRO.exists():
            self.log(f"macro.py not found beside the launcher: {MACRO}")
            self.state = "ERROR"
            return
        steps, bad = self.parse_plan(self.settings.get("plan", ""))
        if bad:
            self.log("Not started - fix the plan in Settings: " + bad[0])
            self.state = "ERROR"
            return
        env = self.macro_env()
        self.plan_sig = ",".join(f"{k}:{n}" for k, n in steps)
        env["CANDY_PLAN"] = self.plan_sig
        resume = self.resume_point(steps)
        if resume:
            env["CANDY_PLAN_POS"] = f"{resume[0]},{resume[1]}"
            self.log(f"Resuming the plan at step {resume[0] + 1} ({resume[1]} done).")
        mode_key = steps[resume[0] if resume else 0][0] if steps else self.adv_key
        if mode_key == "bugmilk":          # old Nightosphere entry: now just the World / Act adventure
            mode_key = "world"
        self.adv_key = mode_key
        try:
            proc = subprocess.Popen(
                [str(self.python_exe()), "-u", str(MACRO), mode_key], cwd=str(HERE), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.process, self.state = None, "ERROR"
            self.log(f"Start failed: {e}")
            return
        self.process = proc
        self.token += 1
        self.state = "STARTING"
        delay = float(self.settings.get("start_delay", 2))
        self.active_mode = mode_key
        self.activity = f"Starting in {delay:g} seconds - click into Roblox" if delay else "Starting"
        self.saw_events = False
        self.wave = None
        self.stop_deadline = None
        self.finish_reason = self.finish_deadline = self.end_reason = self.stop_origin = None
        self.plan_info = None
        self.watch_hold = False
        self.focus_wait = False
        self.pending_alert = None
        self.recoveries = self.best_wave = 0
        self.last_progress = self.last_heartbeat = time.monotonic()
        self.clock(True)
        self.active_at_start = self.elapsed
        self.reader = threading.Thread(target=self.read_output, args=(proc, self.token), daemon=True)
        self.reader.start()
        if self.settings.get("auto_clickthrough", False):
            self.set_clickthrough(True)
            self.auto_ct = True
        self.log(f"Started {self.adv_label(self.adv_key)}. {self.hk_name('visibility')} hides/shows BMO, "
                 f"{self.hk_name('pause')} pauses.")
        if steps:
            self.log("Plan: " + common.plan_text(steps, self.advs) + (" (repeats)" if self.settings.get("plan_loop") else ""))
        limits = self.plan_or_limits_text()
        if limits and not steps:
            self.log(limits.capitalize())

    def read_output(self, proc, token):
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.messages.put((token, line))
        except Exception as e:
            self.messages.put((token, f"Log reader error: {e}"))

    def send_control(self, command):
        if not self.running:
            return False
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
            return True
        except Exception as e:
            self.log(f"{command.title()} command failed: {e}")
            return False

    def pause(self):
        if not self.running or self.state in ("PAUSED", "PAUSING", "STOPPING"):
            return
        if self.send_control("PAUSE"):
            self.state = "PAUSING"
            self.log("Pausing after the current short input action.")

    def resume(self):
        if not self.running or self.state not in ("PAUSED", "PAUSING"):
            return
        if self.send_control("RESUME"):
            self.state = "RUNNING"
            self.last_progress = time.monotonic()
            self.clock(True)
            self.log("Resuming macro.")

    def stop(self, origin="user"):
        """Stops the macro mode only. BMO stays open (use the x button to quit BMO)."""
        if not self.running:
            return
        if self.state == "STOPPING":          # second click = stop waiting politely
            self.kill_process()
            return
        self.log("Stopping current macro mode...")
        self.stop_origin = self.stop_origin or origin
        self.state = "STOPPING"
        self.stop_deadline = time.monotonic() + 8
        if not self.send_control("STOP"):
            self.kill_process()

    def kill_process(self):
        p = self.process
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                self.stop_deadline = time.monotonic() + 3
            except Exception:
                pass

    # ------------------------------------------------------------ macro output
    def handle_line(self, line):
        if line.startswith(EVENT_PREFIX):
            try:
                ev = json.loads(line[len(EVENT_PREFIX):])
            except ValueError:
                return
            self.saw_events = True
            kind = ev.get("event")
            now = time.monotonic()
            if kind == "mode":
                self.active_mode = ev.get("mode")
                self.last_progress = now
                self.file_log(f"· mode {self.active_mode}")
            elif kind == "activity":
                self.activity = str(ev.get("text", ""))
                self.last_progress = now
                if self.state == "STARTING":
                    self.state = "RUNNING"
                self.file_log(f"· {self.activity}")
            elif kind == "wave":
                w = int(ev.get("wave") or 0)
                self.wave = w or None
                self.last_progress = now
                if w == 0:
                    self.best_wave = 0
                elif w > self.best_wave:
                    self.best_wave = w
                    self.recoveries = 0          # real progress: the watchdog starts counting fresh
                self.file_log(f"· wave {w}")
            elif kind == "run":
                m = ev.get("mode")
                if m:
                    self.counts[m] += 1
                self.last_progress = now
                self.recoveries = 0
                self.history_append({"kind": "run", "mode": m, "dur": ev.get("dur"), "wave": ev.get("wave"),
                                     "map": ev.get("map")})
            elif kind == "failure":
                self.failures += 1
                self.history_append({"kind": "failure", "mode": ev.get("mode") or self.active_mode,
                                     "streak": ev.get("streak")})
            elif kind == "state":
                # the macro says it is really frozen now (not just that BMO asked it to pause)
                if ev.get("state") == "paused" and self.state == "PAUSING":
                    self.state = "PAUSED"
                    self.clock(False)
            elif kind == "focus":
                self.focus_wait = bool(ev.get("on"))
                self.last_progress = now
                if self.focus_wait:
                    self.focus_since, self.focus_alerted = now, False
                self.file_log("· waiting for Roblox to be the active window" if self.focus_wait
                              else "· Roblox active again")
            elif kind == "snapshot":
                self.alert_flush(ev.get("path"))
            elif kind == "plan":
                self.plan_info = ev
                self.plan_resume = {"sig": self.plan_sig, "step": int(ev.get("step", 0)),
                                    "done": int(ev.get("done", 0))}
                self.file_log(f"· plan step {int(ev.get('step', 0)) + 1}/{ev.get('total')} "
                              f"{ev.get('mode')} {ev.get('done')}/{ev.get('count') or 'no limit'}")
            elif kind == "plan_done":
                self.plan_resume = None
            elif kind == "finish":
                self.finish_reason = str(ev.get("reason", "finishing"))
                self.finish_deadline = None
                self.log(f"Finishing after this run: {self.finish_reason}.")
            elif kind == "stop_reason":
                self.end_reason = (str(ev.get("kind", "")), str(ev.get("text", "")))
            elif kind == "busy":
                self.watch_hold = bool(ev.get("on"))
                self.last_progress = now
            return
        self.log(line)
        low = line.lower()
        if not self.saw_events:                  # an older macro.py without typed events
            if "paused by bmo" in low and self.state == "PAUSING":
                self.state = "PAUSED"
                self.clock(False)
            elif "run failed (" in low:
                self.failures += 1
            elif "completed runs:" in low:
                self.counts[self.active_mode or self.adv_key] += 1
        if self.state == "STARTING" and ("match running" in low or "starting the match" in low):
            self.state = "RUNNING"

    def finish_process(self):
        p = self.process
        code = p.returncode
        self.process = None
        try:
            p.stdin.close()
        except Exception:
            pass
        self.clock(False)
        asked = self.state == "STOPPING"
        reason = self.end_reason
        bad_reason = reason is not None and reason[0] != "milk"
        self.state = "STOPPED" if ((code == 0 or asked) and not bad_reason) else "ENDED"
        self.stop_deadline = None
        self.activity = ""
        self.wave = None
        if self.auto_ct:
            self.set_clickthrough(False)
        self.log(f"Macro ended (exit {code}).")
        clean = self.alert_on_end(code, asked, reason)
        self.finish_reason = self.finish_deadline = self.plan_info = None
        self.watch_hold = False
        self.focus_wait = False
        self.alert_flush()
        self.save_stats()
        if clean:
            self.run_finish_action()

    def alert_on_end(self, code, asked, reason):
        """One alert per macro exit, unless you stopped it yourself or are quitting BMO.
        Returns True when the macro ended cleanly on purpose (so the finish action may run)."""
        last = " | ".join(list(self.logs)[-3:])
        title = body = None
        clean = False
        if reason:
            title = REASON_TITLES.get(reason[0], "Macro stopped")
            body = reason[1] + ("" if reason[0] == "milk" else f"\nLast: {last}")
            clean = reason[0] == "milk"
        elif self.finish_reason and (not asked or self.stop_origin == "limit"):
            title, body = "Finished cleanly", self.finish_reason[:1].upper() + self.finish_reason[1:]
            clean = True
        elif asked:
            return False
        elif code != 0:
            title, body = "Macro crashed", f"Exit code {code}.\nLast: {last}"
        else:
            title, body = "Macro ended on its own", f"Last: {last}"
        self.notify(title, body, key="end", shot=not clean)
        return clean

    def run_finish_action(self):
        """The 'when it finishes cleanly' setting: close Roblox, sleep, or shut down (Windows only)."""
        action = self.settings.get("on_finish", "none")
        if action == "none" or sys.platform != "win32":
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.log(f"Finish action: {action}.")
        try:
            if action == "close_roblox":
                subprocess.Popen(["taskkill", "/F", "/IM", "RobloxPlayerBeta.exe"], creationflags=flags)
            elif action == "sleep":
                subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=flags)
            elif action == "shutdown":
                subprocess.Popen(["shutdown", "/s", "/t", "90", "/c", "BMO finished: shutting down. "
                                  "Run  shutdown /a  to cancel."], creationflags=flags)
                self.notify("Shutting down in 90 seconds", "BMO finished. Run  shutdown /a  to cancel.",
                            key="finish_action", force=True)
        except Exception as e:
            self.log(f"Finish action failed: {e}")

    def poll(self):
        if self.closing:
            return
        try:
            self.handle_hotkeys()
            self.drain_notes()
            self.housekeeping()
            try:
                while True:
                    token, line = self.messages.get_nowait()
                    if token == self.token:
                        self.handle_line(line)
            except queue.Empty:
                pass
            p = self.process
            if p is not None:
                if p.poll() is not None and (self.reader is None or not self.reader.is_alive()) \
                        and self.messages.empty():
                    self.finish_process()
                elif self.stop_deadline and time.monotonic() > self.stop_deadline:
                    try:
                        p.kill()
                    except Exception:
                        pass
                    self.stop_deadline = None
            if self.quitting and self.process is None:
                self.finish_close()
                return
            if self.running:
                self.watchdog()
                self.check_limits()
                if time.monotonic() - self.last_stats_save > 30:
                    self.save_stats()
            pi = self.plan_info
            sig = (self.state, int(self.elapsed), tuple(self.counts.values()), self.failures,
                   self.activity, len(self.logs), self.log_open, self.minimized, self.clickthrough,
                   self.adv_key, self.active_mode, self.wave, self.finish_reason, self.checking, self.focus_wait,
                   (pi.get("step"), pi.get("done")) if pi else None)
            if sig != self.last_sig and not self.hidden and self.drag is None:
                self.last_sig = sig
                self.draw()
        except Exception as e:
            self.on_tk_error(type(e), e, e.__traceback__)
        self.later("poll", 150, self.poll)

    # ------------------------------------------------------------ housekeeping, watchdog, limits, alerts
    def housekeeping(self):
        """Small periodic jobs, run from poll()."""
        now = time.monotonic()
        self.flush_live()
        self.dash_tick()
        if self.pending_alert and now > self.pending_alert[1]:
            self.alert_flush()                      # the screenshot never came: send the alert without it
        mins = int(self.settings.get("heartbeat_minutes") or 0)
        if self.running and self.state == "RUNNING" and mins > 0:
            if now - self.last_heartbeat >= mins * 60:
                self.last_heartbeat = now
                self.notify("Still running", self.activity or "working", key="heartbeat", force=True)
        else:
            self.last_heartbeat = now
        if self.running and self.focus_wait and not self.focus_alerted and now - self.focus_since > 120:
            # the stuck-watchdog is on hold while input is held for focus, so this is what tells you
            self.focus_alerted = True
            self.notify("Waiting for Roblox", "The macro has been holding all input for 2 minutes because "
                        "Roblox isn't the active window.", key="focus", shot=True)
        if now - self.sched_checked >= 20:
            self.sched_checked = now
            self.check_schedule()

    @staticmethod
    def hhmm(text):
        m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return None
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    def check_schedule(self):
        now, today = time.strftime("%H:%M"), time.strftime("%Y-%m-%d")
        start_at, stop_at = self.hhmm(self.settings.get("start_at")), self.hhmm(self.settings.get("stop_at"))
        if start_at and now == start_at and self.sched_fired.get("start") != today and not self.running:
            self.sched_fired["start"] = today
            self.log(f"Scheduled start ({start_at}).")
            self.start()
        if stop_at and now == stop_at and self.sched_fired.get("stop") != today and self.running:
            self.sched_fired["stop"] = today
            if not self.finish_reason:          # works while paused too: the macro finishes when resumed
                self.request_finish(f"the scheduled stop time ({stop_at})")

    def watchdog(self):
        """No new activity / wave / run for N minutes while farming: ask the macro to recover, tell you,
        and after WATCHDOG_TRIES failed recoveries in a row stop it (so it can't thrash all night)."""
        now = time.monotonic()
        if not (self.settings.get("watchdog", True) and self.state == "RUNNING" and not self.watch_hold
                and not self.focus_wait):
            self.last_progress = now          # paused / starting / waiting for you: the clock starts over
            return
        limit = max(2.0, float(self.settings.get("watchdog_minutes", 5))) * 60
        if now - self.last_progress < limit:
            return
        self.last_progress = now
        mins = int(limit // 60)
        doing = self.activity or "no activity reported"
        if self.recoveries >= WATCHDOG_TRIES:
            self.log(f"Watchdog: still stuck after {WATCHDOG_TRIES} recoveries - stopping the macro.")
            self.notify("Gave up - still stuck",
                        f"No progress for {mins} min after {WATCHDOG_TRIES} recovery tries, so BMO stopped the macro.\n"
                        f"Doing: {doing}", key="stuck")
            self.stop("watchdog")
            return
        self.recoveries += 1
        sent = self.send_control("UNSTICK")
        self.log(f"Watchdog: no change for {mins} min. Recovery {self.recoveries}/{WATCHDOG_TRIES}.")
        self.notify(f"Stuck for {mins} min",
                    f"Nothing changed ({doing}). "
                    + (f"Trying recovery {self.recoveries}/{WATCHDOG_TRIES}." if sent else "Couldn't reach the macro."),
                    key="stuck")

    def check_limits(self):
        if self.state != "RUNNING":
            return
        now = time.monotonic()
        if self.finish_reason:
            if self.finish_deadline and now > self.finish_deadline:
                self.log("Finishing is taking too long - stopping now.")
                self.stop("limit")
            return
        hours = float(self.settings.get("stop_hours") or 0)
        if hours > 0 and self.elapsed - self.active_at_start >= hours * 3600:
            self.request_finish(f"ran for {hours:g} hours")

    def request_finish(self, reason):
        """Ask the macro to stop cleanly after its current run (it force-stops if that takes 20 minutes)."""
        if self.send_control("FINISH"):
            self.finish_reason = reason
            self.finish_deadline = time.monotonic() + 20 * 60
            self.log(f"Finishing after this run: {reason}.")

    def context(self):
        bits = [self.adv_label(self.active_mode or self.adv_key).title()]
        if self.wave:
            bits.append(f"wave {self.wave}")
        bits += [f"{self.total_runs} runs", f"up {self.fmt(self.elapsed)}"]
        return " | ".join(bits)

    def notify(self, title, body="", key=None, force=False, shot=False):
        """Discord webhook and/or Windows notification, sent from a worker thread. With shot=True the Discord
        message gets a picture of the game attached (asked from the running macro, or the newest debug shot)."""
        now = time.monotonic()
        if key and not force and now - self.alert_last.get(key, -1e9) < 30:
            return
        if key:
            self.alert_last[key] = now
        text = f"{body}\n{self.context()}" if body else self.context()
        self.file_log(f"ALERT {title} - {body}")
        hook = str(self.settings.get("discord_webhook") or "").strip()
        toast = bool(self.settings.get("toast_alerts", True))
        if not hook and not toast:
            if force:
                self.notes.put(("alert_result", "No alert channel is on: add a webhook or enable Windows notifications."))
            return
        if self.pending_alert:
            self.alert_flush()
        alert = (title, text, hook, toast)
        if shot and hook and self.settings.get("alert_screenshots", True):
            if self.running and self.send_control("SNAPSHOT"):
                self.pending_alert = (alert, time.monotonic() + 6)      # sent when the picture arrives
                return
            self.deliver(alert, self.recent_shot())
            return
        self.deliver(alert, None)

    def deliver(self, alert, file):
        threading.Thread(target=self.deliver_alert, args=(*alert, file), daemon=True).start()

    def alert_flush(self, path=None):
        """A picture arrived (or took too long): send the alert that was waiting for it."""
        pending, self.pending_alert = self.pending_alert, None
        if pending:
            self.deliver(pending[0], path if path and Path(path).exists() else self.recent_shot())

    @staticmethod
    def recent_shot():
        """The newest macro screenshot from the last 5 minutes (live.jpg or a debug/*.png), or None."""
        best = None
        for pattern in ("*.jpg", "*.png"):
            for p in DEBUG_DIR.glob(pattern):
                if p.name in ("grab.png", "tpl_test.png"):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if time.time() - st.st_mtime < 300 and st.st_size < 7_000_000 and (best is None or st.st_mtime > best[0]):
                    best = (st.st_mtime, p)
        return best[1] if best else None

    def deliver_alert(self, title, text, hook, toast, file=None):
        out = []                                  # worker thread: results go back through the queue
        if hook:
            err = send_discord(hook, title, text, file)
            out.append("Discord sent" if err is None else f"Discord failed ({err})")
        if toast:
            err = windows_toast(title, text.replace("\n", " - "))
            out.append("Windows sent" if err is None else f"Windows failed ({err})")
        self.notes.put(("alert_result", "; ".join(out)))

    def test_alert(self):
        self.set_test_text("Sending...")
        self.notify("Test alert", "If you can read this, BMO's alerts work.", force=True)

    def set_test_text(self, text):
        if self.test_var is not None:
            try:
                self.test_var.set(text)
            except tk.TclError:
                pass

    def drain_notes(self):
        try:
            while True:
                kind, payload = self.notes.get_nowait()
                if kind == "alert_result":
                    self.log("Alert: " + payload)
                    self.set_test_text(payload)
                elif kind == "check":
                    self.show_report(payload)
                elif kind == "oneshot":
                    self.on_oneshot(*payload)
        except queue.Empty:
            pass

    # ------------------------------------------------------------ setup check + folders
    def run_check(self):
        if self.checking:
            return
        if not MACRO.exists():
            self.log(f"macro.py not found beside the launcher: {MACRO}")
            return
        self.checking = True
        self.log("Checking setup (a few seconds)...")
        threading.Thread(target=self.check_worker, args=(self.python_exe(), self.macro_env()), daemon=True).start()

    def check_worker(self, exe, env):
        items = []
        try:
            r = subprocess.run([str(exe), "-u", str(MACRO), "--check"], cwd=str(HERE), env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in r.stdout.splitlines():
                if line.startswith(EVENT_PREFIX):
                    try:
                        ev = json.loads(line[len(EVENT_PREFIX):])
                    except ValueError:
                        continue
                    if ev.get("event") == "check":
                        items.append((ev.get("level", "ok"), str(ev.get("text", ""))))
            if not items:
                tail = " ".join((r.stderr or r.stdout or "").strip().splitlines()[-3:])
                items.append(("bad", "The check gave no results. " + (tail or "Is macro.py the newest version?")))
        except subprocess.TimeoutExpired:
            items.append(("bad", "The check timed out after 90 seconds."))
        except Exception as e:
            items.append(("bad", f"Couldn't run the check: {e}"))
        self.notes.put(("check", items))

    def show_report(self, items):
        self.checking = False
        bad = sum(1 for lvl, _ in items if lvl == "bad")
        warn = sum(1 for lvl, _ in items if lvl == "warn")
        for lvl, text in items:
            self.file_log(f"CHECK {lvl}: {text}")
        summary = ("Everything looks good - safe to walk away." if not (bad or warn) else
                   f"{bad} problem(s) and {warn} warning(s). Fix the red ones before you walk away." if bad else
                   f"{warn} warning(s). Nothing is broken, but read them.")
        self.log("Setup check: " + summary)
        if self.clickthrough:
            self.set_clickthrough(False)
        self.close_report()
        w = tk.Toplevel(self.root)
        self.report_window = w
        w.title("BMO's Setup Check")
        w.geometry(f"680x{max(360, min(600, 250 + 28 * len(items)))}+{self.pos[0] + 30}+{self.pos[1] + 30}")
        w.minsize(520, 320)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close_report)
        body = theme.frame_window(w, "SETUP CHECK")
        foot = tk.Frame(body, bg=theme.T["paper"])
        foot.pack(side="bottom", fill="x", pady=(4, 8))
        tk.Label(foot, text=theme.quote(), bg=theme.T["paper"], fg=theme.T["star"],
                 font=(theme.FONT, 10, "bold")).pack(side="left", padx=16)
        theme.Pill(foot, "CLOSE", self.close_report, "cream", width=90).pack(side="right", padx=16)
        top = tk.Frame(body, bg=theme.T["paper"])
        top.pack(fill="x", padx=12, pady=(2, 4))
        total = max(1, len(items))
        good = total - bad - warn
        hearts = 5 if not (bad or warn) else max(0, min(round(5 * good / total), 3 if bad else 4))
        theme.HeartBar(top, hearts, 5, bg=theme.T["paper"]).pack(side="left")
        tk.Label(top, text=summary, bg=theme.T["paper"], fg=C["red"] if bad else theme.T["brown"],
                 font=(theme.FONT, 9, "bold"), wraplength=440, justify="left").pack(side="left", padx=10)
        frame = tk.Frame(body, bg=theme.T["paper"])
        frame.pack(fill="both", expand=True, padx=10, pady=(2, 4))
        bar = ttk.Scrollbar(frame, orient="vertical")
        bar.pack(side="right", fill="y")
        text = tk.Text(frame, wrap="word", bg=theme.T["paper_lt"], fg=theme.T["brown"], font=("Consolas", 9),
                       relief="flat", padx=12, pady=6, cursor="arrow", yscrollcommand=bar.set,
                       highlightthickness=2, highlightbackground=theme.T["edge"])
        text.pack(side="left", fill="both", expand=True)
        bar.config(command=text.yview)
        text.tag_configure("ok", foreground=C["green"], spacing3=2)
        text.tag_configure("warn", foreground=C["amber"], spacing3=2)
        text.tag_configure("bad", foreground=C["red"], font=("Consolas", 9, "bold"), spacing3=2)
        text.tag_configure("okmark", foreground="#E8343F")
        mark = {"ok": "\u2665 ", "warn": "\u2605 ", "bad": "\u2718 "}
        for lvl, line in items:
            text.insert("end", mark.get(lvl, ""), "okmark" if lvl == "ok" else lvl)
            text.insert("end", line + "\n", lvl)
        text.configure(state="disabled")

    def close_report(self):
        w, self.report_window = self.report_window, None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass

    def open_path(self, path, what):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self.log(f"Couldn't open {what}: {e}")

    def open_debug(self):
        try:
            DEBUG_DIR.mkdir(exist_ok=True)
        except Exception:
            pass
        self.open_path(DEBUG_DIR, "the debug folder")

    def open_logfile(self):
        if LOG_FILE.exists():
            self.open_path(LOG_FILE, "bmo_log.txt")
        else:
            self.log("No bmo_log.txt yet.")

    # ------------------------------------------------------------ closing
    def request_close(self):
        """x button: quits BMO. If a macro is running, the first click only arms it."""
        now = time.monotonic()
        if self.running and now > self.close_armed:
            self.close_armed = now + 4
            self.log("Macro is running - click x again within 4s to stop it and quit BMO.")
            return
        if self.running:
            self.quitting = True
            self.stop("quit")
            self.root.after(9000, self.finish_close)     # backstop
        else:
            self.finish_close()

    def finish_close(self):
        if self.closing:
            return
        self.closing = True
        for job in list(self.after_ids.values()) + [self.save_job]:
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self.settings["window_x"], self.settings["window_y"] = int(self.pos[0]), int(self.pos[1])
        self.settings["corner"] = self.corner
        try:
            common.write_json_atomic(SETTINGS_FILE, {**self.settings, "adventure": self.adv_key}, indent=2)
        except Exception:
            pass
        self.save_stats()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
        if self.settings_window is not None:
            try:
                self.settings_window.destroy()
            except Exception:
                pass
            self.settings_window = None
        if self.help_window is not None:
            try:
                self.help_window.destroy()
            except Exception:
                pass
            self.help_window = None
        self.close_report()
        self.dash_stop()
        self.rt["dirty"] = False        # closing BMO: don't ask about an unsaved routine
        self.close_tools()
        self.calc_close()
        self.test_var = self.plan_var = None
        self.file_log("---- BMO closed ----")
        if self.logfile is not None:
            try:
                self.logfile.close()
            except Exception:
                pass
            self.logfile = None
        self.var_refs.clear()
        gc.collect()
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    r = tk.Tk()
    app = BMO(r)
    r.mainloop()
