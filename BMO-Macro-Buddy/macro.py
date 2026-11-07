"""
Candy Kingdom Defense macro (v4) - pick a mode when it starts:

  1) Challenges        Dungeon Train: pick a car, place units, play, return to hub.
  2) Lich raid         Items (B) -> Your Items -> Lich Portal -> Open -> Enter, then Open Lich -> Next -> Yes
                       to go again, until the portals run out.
  3) World / Act       Choose any world and numbered act or Endless mode.

You can also start one directly:  python macro.py challenges | raid | world

In every mode it keeps pressing Upgrade All when you can afford it and Start Wave /
Skip Wave whenever that button is lit up (pink; it's grey when it can't be used).

Unit placement: the first time you play a map (Candy Kingdom, Ice Kingdom, the Lich map,
Nightosphere) the macro records the unit keys and clicks you make before pressing
Start, saves them to placements/<map>.json and replays them on every later run.
Delete that file to record the map again.

BMO can also run  python macro.py --check  to verify the setup (templates, Roblox window size, display),
--test-templates [names], --grab, --snap and --step '<json>' for its Tools window.

Extra adventures can be written as data in routines/*.json (see bmo_common.py / BMO > TOOLS > Routines).

Controls:  Pause/Resume/Stop are controlled by BMO.   F8 (changeable in BMO settings) = finish placement.
           Mouse to top-left corner = emergency stop.
"""
import ctypes

# Must happen before cv2 / mss / pydirectinput / pynput are imported: if Windows scaling (e.g. 175%) is applied to
# this program, screenshots, window sizes and mouse positions are all "virtualised" and unit placement drifts.
DPI_MODE = "unaware"
try:
    if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):     # per-monitor v2
        DPI_MODE = "per-monitor v2"
except Exception:
    pass
if DPI_MODE == "unaware":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)                              # per-monitor
        DPI_MODE = "per-monitor"
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()                               # system
            DPI_MODE = "system"
        except Exception:
            pass

import functools
import gc
import json
import math
import re
import os
import random
import struct
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import cv2
import mss

# mss 10+ renamed mss.mss() to mss.MSS(); use whichever this install has (no more DeprecationWarning in the log).
_MSS = getattr(mss, "MSS", None) or mss.mss
import numpy as np
import pydirectinput
from pynput import keyboard, mouse

import bmo_common as common   # shared with the BMO launcher (keep it beside this file)

# ---------------- settings ----------------
HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
PLACEMENTS = HERE / "placements"
DEBUG = HERE / "debug"

THRESHOLD = float(os.getenv("CANDY_THRESHOLD", "0.80"))
MATCH_TIMEOUT = int(float(os.getenv("CANDY_MATCH_TIMEOUT", "15")))
CHALLENGE_TIMEOUT = 15 * 60 # max seconds to wait for the end screen
FALLBACK_CAR = 3            # car (1-3, left to right) to run when all bonuses are claimed
UPGRADE_EVERY = float(os.getenv("CANDY_UPGRADE_EVERY", "2.0"))
PLACEMENT_RETRIES = 2      # extra tries for a unit whose placed-count didn't change
WAVE_EVERY = float(os.getenv("CANDY_WAVE_EVERY", "1.5"))
TARGET_WAVE = int(float(os.getenv("CANDY_TARGET_WAVE", "32")))
PORTAL_WORLD = os.getenv("CANDY_WORLD", "nightosphere").strip().lower()   # world picked by the "World / Act" adventure
PORTAL_ACT = os.getenv("CANDY_ACT", "infinite").strip().lower()           # 1-5, infinite, or wish (Wish Run)
MATCH_TIMEOUTS = {"challenge": 15 * 60, "act": 15 * 60, "raid": 25 * 60, "infinite": 75 * 60, "wish": 75 * 60}  # max seconds per match
MAPS = ("candy", "ice")     # templates/map_<name>.png: the map label on each car card
CARD_GO_OFFSET = (71, 117)  # Go button center, relative to the top-left of a templates/card_<area>.png
ZONES_FILE = HERE / "zones.json"  # where units may go on each map (lines, relative to the Start Wave button)
ZONE_STEP = 24              # pixels between spots tested along a zone line
SPOT_GAP = 30               # don't test spots closer than this to a unit we already placed
HOVER_WAIT = 0.28           # seconds to let the game show "Click to place" / the red error after the cursor moves
TOAST_THRESHOLD = 0.72     # how well the green check / red X icon must match (real ones score 0.85+, scenery ~0.5)
ROAD_MARGIN = 8             # extra pixels kept clear around the road outlines in zones.json (fences, edge)
AVOID_ROAD = True           # never hover on the road when looking for a spot
REFS = HERE / "refs"       # how each map looks from the usual spawn spot (used to follow the camera)
REF_ANCHOR = (1137, 597)    # where the Start Wave button sits in those reference views
MAX_DEBUG_PICS = 15         # debug screenshots kept (older ones are deleted)
MAX_SPOTS = 45              # most spots tested for one unit before giving up on it
STOP_RUNS = int(float(os.getenv("CANDY_STOP_RUNS", "0")))   # finish cleanly after this many completed runs (0 = never)
STOP_ON_MILK = os.getenv("CANDY_STOP_ON_MILK", "0") == "1"  # raid mode: stop (not carry on / switch to challenges) when the Lich Portals run out
def parse_record_key(name):
    """A key name like f8, insert, home, or one character -> a pynput key. Raises ValueError if it isn't one."""
    name = str(name or "").strip().lower()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return keyboard.Key[name]
    except KeyError:
        raise ValueError(f"unknown key {name!r}")


def record_key_label():
    k = PLACEMENT_KEY
    return (getattr(k, "name", None) or getattr(k, "char", None) or "F8").upper()


try:    # the key that finishes recording unit placement (F9 opens Roblox's developer console); BMO can change it
    PLACEMENT_KEY = parse_record_key(os.getenv("CANDY_RECORD_KEY", "f8"))
except ValueError:
    print("CANDY_RECORD_KEY isn't a valid key name; using F8.", flush=True)
    PLACEMENT_KEY = keyboard.Key.f8
WAIT_SCALE = max(0.3, min(2.0, float(os.getenv("CANDY_WAIT_SCALE", "0.8"))))   # multiplies every pause: 0.5 = twice as fast, 1.0 = the old speed
START_DELAY = float(os.getenv("CANDY_START_DELAY", "2"))       # seconds between Start and the first click
FOCUS_GUARD = os.getenv("CANDY_FOCUS_GUARD", "1") == "1"       # hold all input while Roblox is not the active window
FOCUS_REFOCUS = float(os.getenv("CANDY_FOCUS_REFOCUS", "0"))   # 0 = just wait; N = bring Roblox back after N seconds
KEEP_AWAKE = os.getenv("CANDY_KEEP_AWAKE", "1") == "1"         # stop Windows sleeping / blanking the screen while running
UNDER_BMO = os.getenv("CANDY_UNDER_BMO") == "1"                # started by the BMO launcher (it owns our stdin)
FREEZE_SECS = float(os.getenv("CANDY_FREEZE_SECS", "150"))     # a match screen that doesn't change at all for this long = frozen game (0 = off)
INTERRUPT_EVERY = float(os.getenv("CANDY_INTERRUPT_EVERY", "1.5"))  # how often the pop-up handlers look at the screen
INTERRUPTS_FILE = HERE / "routines" / "_interrupts.json"       # your own pop-ups to dismiss, see load_interrupts()
WALL_STOP = os.getenv("BMO_WALL_STOP", "1") != "0"             # while replaying a recorded walk, let go of the key when the character hits a wall
WALL_STALL = float(os.getenv("BMO_WALL_STALL", "0.5"))         # seconds the screen must stand still (after moving) to count as a wall
WALL_MOTION = float(os.getenv("BMO_WALL_MOTION", "3.0"))       # how much the ground must change between looks to count as "moving"
WALL_STILL = float(os.getenv("BMO_WALL_STILL", "1.2"))         # ...and how little to count as "standing still"
BAD_SPOTS_FILE = PLACEMENTS / "bad_spots.json"                 # spots the game keeps refusing, remembered per map
BAD_SPOT_STRIKES = 3                                           # refusals (net of successes) before a spot is skipped
LIVE_SHOT = DEBUG / "live.jpg"                                 # latest snapshot for BMO and the remote dashboard
ROUTINES, ROUTINE_ERRORS = common.load_routines(HERE)         # routines/*.json: adventures described as data
ADVENTURES = common.build_adventures(ROUTINES)
# ------------------------------------------

pydirectinput.PAUSE = 0.05
pydirectinput.FAILSAFE = True

try:  # make screen coordinates match real pixels on scaled Windows displays
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

stop_flag = threading.Event()
pause_flag = threading.Event()
go_flag = threading.Event()
finish_flag = threading.Event()   # BMO: stop cleanly once the current run is done
unstick_flag = threading.Event()  # BMO watchdog: abandon the current step and recover
in_run = [False]                  # Unstick may only interrupt while a run is being played
pending_mode = [None]      # an adventure key, set by BMO (or the plan), applied between runs
snapshot_flag = threading.Event()  # BMO asked for a picture of the game (served by the main thread)
guard_ready = [False]      # the focus guard only starts once the start-up countdown is over


def parse_plan(text):
    """"raid:5,challenges:3" -> [["raid", 5], ["challenges", 3]] (0 = until stopped). BMO has already checked it."""
    steps, _bad = common.parse_plan(text, ADVENTURES)
    return [[k, n] for k, n in steps]


PLAN = parse_plan(os.getenv("CANDY_PLAN", ""))          # chained plan set up in BMO
PLAN_LOOP = os.getenv("CANDY_PLAN_LOOP", "0") == "1"    # start the plan over when it ends
plan_pos = [0, 0]          # [current step, runs done in that step]
if PLAN and os.getenv("CANDY_PLAN_POS"):                # BMO resuming a plan where it stopped last time
    try:
        _s, _d = (int(x) for x in os.getenv("CANDY_PLAN_POS").split(","))
        if 0 <= _s < len(PLAN):
            plan_pos[:] = [_s, max(0, _d)]
    except ValueError:
        pass
current_map = None
run_info = {"t0": None, "wave": 0}   # when this match started / best wave seen (for BMO's run history)


class Unstick(BaseException):
    """BMO's watchdog saw no progress for minutes: abandon the current step and recover.
    (BaseException so the broad 'except Exception' handlers can't swallow it.)"""


_pause_acked = [False]
_focus = {"checked": 0.0, "busy": False}


def serve_snapshot_request():
    """BMO asked for a picture; take it here on the main thread (screen grabs are not thread-safe)."""
    if snapshot_flag.is_set():
        snapshot_flag.clear()
        try:
            do_snapshot()
        except Exception as e:
            log(f"Snapshot failed: {e}")


def _fg_hwnd():
    try:
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = wintypes.HWND
        return u.GetForegroundWindow()
    except Exception:
        return None


def roblox_active():
    """True when the Roblox window has the keyboard focus (or when we can't tell, so we never block for nothing)."""
    win = roblox_window()
    if win is None:
        return True
    try:
        fg, mine = _fg_hwnd(), getattr(win, "_hWnd", None)
        if fg is None or mine is None:
            return True
        return int(fg) == int(mine)
    except Exception:
        return True


def focus_ok():
    """True when input may be sent. If Roblox lost focus, hold here until it is back (or the user pauses/stops).
    Clicking while another program has focus would type into / click that program instead of the game."""
    if _focus["busy"]:
        return True
    now = time.monotonic()
    if now - _focus["checked"] < 0.5:
        return True
    _focus["checked"] = now
    if roblox_active():
        return True
    time.sleep(0.3)                  # a pop-up flicking past is not a reason to hold everything up
    if roblox_active():
        return True
    _focus["busy"] = True
    log("Roblox isn't the active window - holding all input until it is.")
    event("focus", on=True)
    last_try = time.monotonic()
    try:
        while not roblox_active():
            if stop_flag.is_set():
                raise KeyboardInterrupt
            if pause_flag.is_set():
                return False         # let the pause logic take over, then check again
            serve_snapshot_request()
            if FOCUS_REFOCUS and time.monotonic() - last_try >= FOCUS_REFOCUS:
                last_try = time.monotonic()
                focus_roblox()
            time.sleep(0.25)
    finally:
        _focus["busy"] = False
        event("focus", on=False)
    log("Roblox is active again - carrying on.")
    return True


def control_checkpoint():
    """Wait here while paused (or while Roblox isn't the active window); raise KeyboardInterrupt when BMO
    requests a stop and Unstick when its watchdog does."""
    while True:
        if pause_flag.is_set():
            if not _pause_acked[0]:
                _pause_acked[0] = True
                event("state", state="paused")       # BMO shows PAUSED when it gets this, not when it asked
            while pause_flag.is_set():
                if stop_flag.is_set():
                    raise KeyboardInterrupt
                serve_snapshot_request()
                time.sleep(0.10)
            _pause_acked[0] = False
            event("state", state="running")
        if stop_flag.is_set():
            raise KeyboardInterrupt
        serve_snapshot_request()
        if FOCUS_GUARD and guard_ready[0] and not focus_ok():
            continue
        break
    if unstick_flag.is_set() and in_run[0]:
        raise Unstick


def run_guarded(fn, *args):
    """Call one run; only inside it can the watchdog's Unstick interrupt us."""
    in_run[0] = True
    try:
        return fn(*args)
    finally:
        in_run[0] = False


LIVE_SETTINGS = {   # BMO "SET name value": name -> (module variable, converter). Applies without a restart.
    "threshold": ("THRESHOLD", float),
    "wait_scale": ("WAIT_SCALE", lambda v: max(0.3, min(2.0, float(v)))),
    "match_timeout": ("MATCH_TIMEOUT", lambda v: int(float(v))),
    "upgrade_every": ("UPGRADE_EVERY", float),
    "wave_every": ("WAVE_EVERY", float),
    "target_wave": ("TARGET_WAVE", lambda v: int(float(v))),
    "world": ("PORTAL_WORLD", lambda v: str(v).strip().lower()),
    "act": ("PORTAL_ACT", lambda v: str(v).strip().lower()),
    "stop_runs": ("STOP_RUNS", lambda v: int(float(v))),
    "stop_on_milk": ("STOP_ON_MILK", lambda v: str(v) == "1"),
    "focus_guard": ("FOCUS_GUARD", lambda v: str(v) == "1"),
    "focus_refocus": ("FOCUS_REFOCUS", float),
    "record_key": ("PLACEMENT_KEY", parse_record_key),
    "portal_walk": ("PORTAL_WALK_SECS", lambda v: max(0.5, min(5.0, float(v)))),
}


def control_reader():
    """Read PAUSE / RESUME / STOP / MODE / FINISH / UNSTICK / SET / SNAPSHOT commands from BMO over stdin."""
    def pipe_lost():
        if UNDER_BMO:
            # BMO crashed or was killed: nobody can stop us any more, so don't keep clicking unattended
            log("BMO is gone (the control pipe closed) - stopping.")
            pause_flag.clear()
            stop_flag.set()

    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            pipe_lost()
            return
        if not line:
            pipe_lost()
            return
        cmd, _, arg = line.strip().partition(" ")
        cmd = cmd.upper()
        if cmd == "PAUSE":
            pause_flag.set()
            log("Paused by BMO.")
        elif cmd == "RESUME":
            pause_flag.clear()
            log("Resumed by BMO.")
        elif cmd == "MODE":
            pending_mode[0] = arg.strip().lower()
            log(f"Mode switch to {pending_mode[0]} queued - it starts when the current run ends.")
        elif cmd == "FINISH":
            request_finish("BMO asked me to finish")
        elif cmd == "UNSTICK":
            unstick_flag.set()
            log("BMO's watchdog says nothing has changed for a while - trying to recover.")
        elif cmd == "SNAPSHOT":
            snapshot_flag.set()
        elif cmd == "SET":
            name, _, value = arg.partition(" ")
            spec = LIVE_SETTINGS.get(name)
            if spec:
                try:
                    globals()[spec[0]] = spec[1](value.strip())
                except ValueError:
                    log(f"Ignored a bad value for {name}: {value!r}")
        elif cmd == "STOP":
            pause_flag.clear()
            stop_flag.set()
            log("Stop requested by BMO.")
            return


mouse_pos = [640, 360]   # last place we put the cursor (used to nudge it while idle)


class Disconnected(Exception):
    """Raised when Roblox shows the Disconnected / Reconnect dialog."""


class Frozen(Exception):
    """The match screen has not changed at all for FREEZE_SECS: the game (or Roblox) is hung."""


def on_key(key):
    if key == PLACEMENT_KEY:
        go_flag.set()


keyboard.Listener(on_press=on_key, daemon=True).start()


def log(msg):
    print(time.strftime("[%H:%M:%S]"), msg, flush=True)


_last_activity = [None]


def event(name, /, **data):
    """Structured line for the BMO overlay (it hides these from its log).
    `name` is positional-only so events can carry a field called "kind" (stop_reason does).
    This used to be `event(kind, **data)`, which made every stop_reason() call raise a TypeError."""
    print("@BMO " + json.dumps({"event": name, **data}), flush=True)


def stop_reason(kind, text):
    """Tell BMO why the macro is about to stop on its own, so it can raise an alert."""
    event("stop_reason", kind=kind, text=text)


def activity(text):
    """Tell BMO what we're doing right now (only when it changes)."""
    if text != _last_activity[0]:
        _last_activity[0] = text
        event("activity", text=text)


def sleep(seconds):
    """Pause-aware sleep that wakes promptly for BMO stop/pause commands."""
    end = time.monotonic() + seconds * WAIT_SCALE
    while time.monotonic() < end:
        control_checkpoint()
        time.sleep(min(0.05, max(0.0, end - time.monotonic())))


def is_memory_error(e):
    return isinstance(e, MemoryError) or "Insufficient memory" in str(e) or "OutOfMemory" in str(e)


def memory_info():
    """(free MB of RAM, this program's memory MB) on Windows; (None, None) if it can't be read."""
    try:
        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong),
                        ("ap", ctypes.c_ulonglong), ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ev", ctypes.c_ulonglong)]
        ms = MS(); ms.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))

        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("pf", ctypes.c_ulong), ("peak", ctypes.c_size_t),
                        ("ws", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("pagefile", ctypes.c_size_t),
                        ("peakpf", ctypes.c_size_t)]
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        h = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        return ms.ap // 2**20, pmc.ws // 2**20
    except Exception:
        return None, None


def memory_report(prefix="Memory"):
    free, mine = memory_info()
    bits = struct.calcsize("P") * 8
    if free is None:
        log(f"{prefix}: Python is {bits}-bit.")
    else:
        log(f"{prefix}: {free} MB of RAM free, the macro is using {mine} MB, Python is {bits}-bit.")


def keep_awake(on):
    """While the macro runs, stop Windows from sleeping or blanking the screen (a blank or locked screen
    gives black frames, and the macro would think the game froze). Must be called from the main thread."""
    if not KEEP_AWAKE:
        return
    try:
        # ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        flags = 0x80000000 | (0x1 | 0x2 if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
    except Exception:
        pass


# ---------------- vision ----------------
_templates = {}


_loaded_res = [None]
_res_cache = {"sc": None, "t": 0.0, "folder": None}
STD_SIZES = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160))


def res_folder():
    """Name of the templates/<width>x<height> subfolder to prefer at the current scale (e.g. "1920x1080"), or None.
    Pictures that Roblox draws at a fixed pixel size can be captured once per resolution and put there; any
    picture not in that folder falls back to the main templates/ folder."""
    if _LOCK["on"]:
        return _LOCK["folder"]                # from the last CHECK SETUP: no scanning the templates folder
    sc = ui_scale()
    if _res_cache["sc"] is not None and abs(_res_cache["sc"] - sc) < 0.005 and time.time() - _res_cache["t"] < 30:
        return _res_cache["folder"]
    best = None
    try:
        for d in TEMPLATES.iterdir():
            m = re.fullmatch(r"(\d+)x(\d+)", d.name)
            if d.is_dir() and m:
                diff = abs(int(m.group(1)) / REF_WINDOW_W - sc)
                if diff < 0.08 and (best is None or diff < best[0]):
                    best = (diff, d.name)
    except OSError:
        pass
    _res_cache.update(sc=sc, t=time.time(), folder=best[1] if best else None)
    return _res_cache["folder"]


def capture_res_name():
    """Folder name templates captured now belong in: "" for a ~1280x720 window, else e.g. "1920x1080"."""
    sc = ui_scale()
    if abs(sc - 1.0) < 0.01:
        return ""
    w = REF_WINDOW_W * sc
    for ww, hh in STD_SIZES:
        if abs(ww / w - 1) < 0.06:
            return f"{ww}x{hh}"
    return f"{int(round(w))}x{int(round(720 * sc))}"


def template(name, required=True):
    fold = res_folder()
    if fold != _loaded_res[0]:
        _templates.clear()
        _variant_cache.clear()
        _loaded_res[0] = fold
        if fold:
            log(f"Using the pictures in templates/{fold}/ first (others come from templates/).")
    if name not in _templates:
        special = TEMPLATES / fold / f"{name}.png" if fold else None
        img = cv2.imread(str(special)) if special is not None and special.exists() else None
        plain = TEMPLATES / f"{name}.png"
        if img is None and plain.exists():       # (probing for pictures that don't exist is normal for variants)
            img = cv2.imread(str(plain))
        if img is None and required:
            sys.exit(f"Missing template: templates/{name}.png")
        _templates[name] = img
    return _templates[name]


def retry_on_memory(fn, what):
    """Run fn(); if Windows says it's out of memory, free what we can, wait, and try again a few times
    instead of crashing (a long AFK session can leave the PC short on RAM for a moment)."""
    for attempt in range(1, 7):
        try:
            return fn()
        except (cv2.error, MemoryError) as e:
            if not is_memory_error(e):
                raise
            gc.collect()
            if attempt == 1:
                memory_report(f"Ran out of memory during {what}")
            if attempt == 6:
                raise
            sleep(2 * attempt)


_sct_local = threading.local()
_pinned = [None]           # while set, screenshot() returns this frame (one real grab per polling tick)
_gray_cache = [None, None]
_hsv_cache = [None, None]


def _grab_raw():
    for attempt in (1, 2):
        sct = getattr(_sct_local, "sct", None)
        try:
            if sct is None:
                sct = _sct_local.sct = _MSS()      # kept open: creating one per grab was slow and leaky
            shot = np.array(sct.grab(sct.monitors[1]))
            frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
            _raw_size[:] = [frame.shape[1], frame.shape[0]]
            sc = ui_scale()
            if abs(sc - 1.0) >= 0.01:     # work on a virtual screen where the Roblox window is ~1280 wide
                frame = cv2.resize(frame, None, fx=1 / sc, fy=1 / sc,
                                   interpolation=cv2.INTER_AREA if sc > 1 else cv2.INTER_CUBIC)
            return frame
        except Exception as e:
            try:
                if sct is not None:
                    sct.close()
            except Exception:
                pass
            _sct_local.sct = None
            if attempt == 2 or is_memory_error(e):
                raise
            time.sleep(0.2)                            # a locked screen / display change: retry with a fresh handle


def screenshot():
    if _pinned[0] is not None:
        return _pinned[0]
    return retry_on_memory(_grab_raw, "a screenshot")


@contextmanager
def pinned_frame(frame):
    """Inside this block every screenshot() is the same frame, so checking several pictures costs one grab."""
    previous, _pinned[0] = _pinned[0], frame
    try:
        yield frame
    finally:
        _pinned[0] = previous


def gray_of(screen):
    if _gray_cache[0] is not screen:
        _gray_cache[1] = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        _gray_cache[0] = screen
    return _gray_cache[1]


def hsv_of(screen):
    if _hsv_cache[0] is not screen:
        _hsv_cache[1] = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
        _hsv_cache[0] = screen
    return _hsv_cache[1]


def match(screen, tpl):
    return retry_on_memory(lambda: cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED), "image matching")


_rect_cache = {"t": 0.0, "rect": None, "win": None}


def real_rect():
    """(left, top, right, bottom) of the Roblox window in REAL screen pixels, or None if it can't be found."""
    if time.time() - _rect_cache["t"] < 2:
        return _rect_cache["rect"]
    rect, win = None, None
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getWindowsWithTitle("Roblox") if w.width > 300 and not w.isMinimized]
        exact = [w for w in wins if w.title.strip() == "Roblox"]
        win = (exact or wins or [None])[0]
        if win:
            rect = (win.left, win.top, win.left + win.width, win.top + win.height)
    except Exception:
        pass
    _rect_cache.update(t=time.time(), rect=rect, win=win)
    return rect


def roblox_window():
    """The Roblox window object (same one roblox_rect measures), or None."""
    real_rect()
    return _rect_cache["win"]


# ---------------- resolution independence ----------------
# Every picture, pixel offset and wave-digit shape in this program was made for a Roblox window about
# 1280x720. Rather than redo them for every resolution, the macro works on a "virtual 1280-wide screen":
# the real screen is shrunk/enlarged by 1/UI_SCALE before matching, and every mouse position is multiplied
# by UI_SCALE on its way out. A window already close to 1280x720 gets UI_SCALE 1.0 = exactly the old behaviour.
# BMO_UI_SCALE=off turns this off; BMO_UI_SCALE=1.5 forces a number; the default is "auto".
REF_WINDOW_W = 1280
NATIVE_BAND = (1200, 1420, 680, 820)      # window sizes that already work without scaling (w min/max, h min/max)
_UI_SCALE_MODE = os.environ.get("BMO_UI_SCALE", "auto").strip().lower()
_raw_size = [0, 0]                        # size of the last real screen grab (fallback when no window is found)
_scale_cache = {"t": 0.0, "s": 1.0}

# ---- learned template sizes, remembered between launches (scale_hints.json, keyed by Roblox window size) ----
HINTS_FILE = HERE / "scale_hints.json"
_scale_hint = {}          # template name -> the size factor that matched last time (tried first); current resolution only
_hint_stale = set()       # names whose hint came from the file and hasn't been confirmed yet this session
_hint_res = [None]        # "WxH" the hints above belong to


def _read_hint_file():
    try:
        data = json.loads(HINTS_FILE.read_text(encoding="utf-8"))
        hints = data.get("hints", {}) if isinstance(data, dict) else {}
        return hints if isinstance(hints, dict) else {}
    except Exception:
        return {}


def _save_hints():
    """Write the current resolution's hints into scale_hints.json (other resolutions are kept). Never raises."""
    if not _hint_res[0]:
        return
    try:
        allhints = _read_hint_file()
        allhints[_hint_res[0]] = {k: round(float(v), 3) for k, v in _scale_hint.items()}
        tmp = HINTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "hints": allhints}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, HINTS_FILE)
    except Exception:
        pass


def _use_hint_res(res):
    """Switch to the hints saved for this window size (called whenever the window size is measured)."""
    if res == _hint_res[0]:
        return
    _hint_res[0] = res
    saved = _read_hint_file().get(res, {})
    _scale_hint.clear()
    _hint_stale.clear()
    for k, v in saved.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.3 <= f <= 6.0:
            _scale_hint[str(k)] = f
            _hint_stale.add(str(k))
    if saved:
        log(f"Loaded {len(_scale_hint)} saved picture sizes for {res} (scale_hints.json).")


# ---- resolution lock: CHECK SETUP saves the resolution it found; Start then trusts it ----
# Without it every Start re-measures the window, re-learns which size each picture is drawn at, and falls back to
# searching ~35 sizes for anything it hasn't seen. With it the scale, the picture folder and the learned sizes are
# fixed up front, every picture is loaded and pre-sized during the Start countdown, and a picture that matches at
# its remembered size is accepted straight away. Run CHECK SETUP again after changing resolution. BMO_RES_LOCK=off
# turns this off (back to detecting everything on every Start).
RES_PROFILE_FILE = HERE / "resolution_profile.json"
RES_LOCK_ENABLED = os.environ.get("BMO_RES_LOCK", "on").strip().lower() not in ("off", "0", "no", "false")
LOCK_EARLY_SCORE = 0.90        # a picture that matches this well at its remembered size is not tried at other sizes
_LOCK = {"on": False, "res": None, "scale": None, "folder": None, "window": None}


def current_window_size():
    """(width, height) of the Roblox window in real pixels, or of the captured display when no window is found."""
    rect = real_rect()
    if rect:
        return rect[2] - rect[0], rect[3] - rect[1]
    return tuple(_raw_size)


def same_window_size(a, b):
    """Is window size a (w, h) the same resolution as b? A title bar or taskbar changes the height a little."""
    return abs(a[0] - b[0]) <= 40 and abs(a[1] - b[1]) <= 110


def save_res_profile(ww, wh):
    """Remember what CHECK SETUP found (never raises). Returns the label shown to the person."""
    label = supported_resolution(ww, wh) or f"{ww}x{wh}"
    try:
        data = {"version": 1, "res": label, "window": [int(ww), int(wh)], "scale": round(ui_scale(), 4),
                "folder": res_folder() or "", "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = RES_PROFILE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, RES_PROFILE_FILE)
    except Exception as e:
        log(f"Couldn't save {RES_PROFILE_FILE.name}: {e}")
    return label


def prime_res_profile():
    """Called at Start, before the countdown. Reads the saved resolution and (provisionally) locks it so the pictures
    can be loaded while you click into Roblox. confirm_res_profile() checks it against the real window afterwards.
    Returns True if a lock was applied."""
    if not RES_LOCK_ENABLED:
        return False
    try:
        data = json.loads(RES_PROFILE_FILE.read_text(encoding="utf-8"))
        win = (int(data["window"][0]), int(data["window"][1]))
        scale = max(0.4, min(5.0, float(data["scale"])))
    except FileNotFoundError:
        log("No saved resolution yet. Click LOG > CHECK SETUP once (with Roblox open) so BMO can lock it in "
            "and start faster. Detecting it automatically for now.")
        return False
    except Exception:
        log(f"{RES_PROFILE_FILE.name} couldn't be read. Run CHECK SETUP again. Detecting the resolution automatically for now.")
        return False
    _LOCK.update(on=True, res=str(data.get("res") or ""), scale=scale, folder=str(data.get("folder") or "") or None,
                 window=win)
    _res_cache.update(sc=scale, t=time.time(), folder=_LOCK["folder"])
    _use_hint_res(f"{win[0]}x{win[1]}")
    _hint_stale.clear()                       # sizes learned at this resolution are trusted, not re-verified
    return True


def confirm_res_profile():
    """Called once the countdown is over (you are in Roblox): is the window still the resolution that was saved?
    If not, drop the lock and go back to detecting everything, so a changed resolution never uses the wrong scale."""
    if not _LOCK["on"]:
        return
    try:
        screenshot()                          # also measures the display when there is no window to measure
        now = current_window_size()
    except Exception:
        now = None
    if now and same_window_size(now, _LOCK["window"]):
        log(f"Resolution locked from your last check-up: {_LOCK['res']} (auto-scale x{_LOCK['scale']:.2f}). "
            "Run CHECK SETUP again if you change it.")
        return
    was = f"{_LOCK['window'][0]}x{_LOCK['window'][1]}"
    log(f"Your Roblox window is {now[0]}x{now[1]} but the last check-up was {was}. "
        "Run LOG > CHECK SETUP again to lock the new resolution. Detecting it automatically for now."
        if now else "Couldn't measure the Roblox window to confirm the saved resolution. Detecting it automatically.")
    _LOCK.update(on=False, res=None, scale=None, folder=None, window=None)
    _templates.clear()
    _variant_cache.clear()
    _loaded_res[0] = None
    _res_cache.update(sc=None, t=0.0, folder=None)
    _scale_cache["t"] = 0.0
    _hint_res[0] = None


def warm_templates():
    """Load every picture and pre-size it for this resolution once (runs while the Start countdown ticks), so the
    first runs don't pay for it. Only used with a locked resolution."""
    t0 = time.time()
    n = 0
    try:
        names = {f.stem for f in TEMPLATES.glob("*.png")}
        if _LOCK["folder"] and (TEMPLATES / _LOCK["folder"]).is_dir():
            names |= {f.stem for f in (TEMPLATES / _LOCK["folder"]).glob("*.png")}
        for name in sorted(names):
            if not _LOCK["on"]:
                return                        # the lock was dropped (different resolution): stop wasting time
            if template(name, required=False) is None:
                continue
            for f in _factors_for(name):
                _sized(name, f)
            n += 1
        log(f"Loaded {n} pictures for {_LOCK['res']} in {time.time() - t0:.1f}s.")
    except Exception as e:
        log(f"Picture warm-up stopped early ({type(e).__name__}: {e}); the rest load as they are needed.")


def ui_scale():
    """How many real pixels one virtual pixel is. 1.0 for a ~1280x720 window."""
    if _UI_SCALE_MODE in ("off", "0", "no", "false"):
        return 1.0
    if _UI_SCALE_MODE not in ("auto", ""):
        try:
            return max(0.4, min(5.0, float(_UI_SCALE_MODE)))
        except ValueError:
            return 1.0
    if _LOCK["on"]:
        return _LOCK["scale"]                 # from the last CHECK SETUP: no re-measuring the window
    if time.time() - _scale_cache["t"] < 2:
        return _scale_cache["s"]
    rect = real_rect()
    if rect:
        ww, wh = rect[2] - rect[0], rect[3] - rect[1]
    else:
        ww, wh = _raw_size                # no window found (fullscreen?): assume Roblox fills the display
    sc = 1.0
    if ww > 300 and wh > 200:
        _use_hint_res(f"{ww}x{wh}")
        lo_w, hi_w, lo_h, hi_h = NATIVE_BAND
        if not (lo_w <= ww <= hi_w and lo_h <= wh <= hi_h):
            sc = max(0.4, min(5.0, ww / REF_WINDOW_W))
            for sw, _sh in STD_SIZES:      # a window a few pixels wider than 1920 (borders) is still exactly 1.5x
                if abs(ww - sw) <= 40:
                    sc = sw / REF_WINDOW_W
                    break
    if abs(sc - _scale_cache["s"]) > 0.01 and _scale_cache["t"]:
        log(f"Roblox window is now {ww}x{wh}: auto-scale x{sc:.2f}.")
    _scale_cache.update(t=time.time(), s=sc)
    return sc


SUPPORTED_RESOLUTIONS = ((1280, 720), (1920, 1080))
SUPPORTED_RESOLUTIONS_TEXT = tuple(f"{w}x{h}" for w, h in SUPPORTED_RESOLUTIONS)


def supported_resolution(ww, wh):
    """'1280x720' / '1920x1080' if the Roblox window is one of the supported sizes, else None.
    The width decides the scale, so it must be close. A windowed Roblox is a little taller than its picture (title
    bar) or a little shorter when maximised (taskbar), so the height gets more slack."""
    for w, h in SUPPORTED_RESOLUTIONS:
        if abs(ww - w) <= 40 and -110 <= wh - h <= 80:
            return f"{w}x{h}"
    return None


def windows_display_scale():
    """Windows 'Scale' setting of the main display as a percent (100, 125, ...), or None if it can't be read."""
    try:
        return int(ctypes.windll.shcore.GetScaleFactorForDevice(0))
    except Exception:
        return None


def roblox_rect():
    """(left, top, right, bottom) of the Roblox window in VIRTUAL (1280-wide) screen pixels, or None."""
    rect = real_rect()
    if rect is None:
        return None
    sc = ui_scale()
    return rect if abs(sc - 1.0) < 0.01 else tuple(v / sc for v in rect)


_real_moveTo = pydirectinput.moveTo


def _scaled_moveTo(x, y, *args, **kwargs):
    sc = ui_scale()
    if abs(sc - 1.0) >= 0.01:
        x, y = int(round(x * sc)), int(round(y * sc))
    return _real_moveTo(x, y, *args, **kwargs)


pydirectinput.moveTo = _scaled_moveTo     # all the macro's cursor moves are in virtual coordinates


_last_report = [""]


def display_report():
    """Say which display the macro is looking at and where the Roblox window is, and warn about the
    things that break it (window on another display, odd window size). Only logs when something changed,
    so switching between your monitor and your laptop shows up in the log."""
    try:
        with _MSS() as sct:
            mons = list(sct.monitors)
    except Exception:
        return
    parts = ["Displays: " + ", ".join(f"{m['width']}x{m['height']} at ({m['left']},{m['top']})" for m in mons[1:])
             + ". The macro looks at and clicks display 1 only."]
    rect = real_rect()
    if rect:
        ww, wh = rect[2] - rect[0], rect[3] - rect[1]
        parts.append(f"Roblox window: {ww}x{wh} at ({rect[0]},{rect[1]}).")
        m = mons[1]
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        if not (m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]):
            parts.append("WARNING: the Roblox window is NOT on display 1, so the macro can't see or click it. "
                         "Drag it onto the main display (or make this display the main one in Windows display settings).")
        elif abs(ui_scale() - 1.0) >= 0.01:
            parts.append(f"Auto-scale is on (x{ui_scale():.2f}): the macro treats this window as if it were 1280 wide.")
            if not (1.55 <= ww / max(1, wh) <= 2.0):
                parts.append("WARNING: this window isn't close to 16:9, so the game's buttons may be laid out "
                             "differently and matching can be unreliable. A 16:9 window works best.")
    else:
        parts.append("Couldn't find a window titled Roblox (normal in fullscreen).")
    pct = windows_display_scale()
    if pct and pct != 100:
        parts.append(f"Windows display scale is {pct}% (this program is DPI-aware: {DPI_MODE}).")
    if DPI_MODE == "unaware":
        parts.append("WARNING: couldn't make this program DPI-aware, so screenshots and clicks may be off on a scaled display.")
    if rect and abs(ui_scale() - 1.5) < 0.1:
        ww, wh = rect[2] - rect[0], rect[3] - rect[1]
        if abs(wh - 1080) > 6:
            parts.append(f"NOTE: at 1920x1080 the Roblox window is {ww}x{wh}, so it has a title bar or taskbar gap and "
                         "the game's picture is not exactly 16:9. Press F11 in Roblox (fullscreen) for the most "
                         "consistent unit placement.")
    text = " ".join(parts)
    if text != _last_report[0]:
        _last_report[0] = text
        log(text)


_variant_cache = {}


def size_factors():
    """Template sizes to try. UI that scales with the window matches at 1.0 (the screen is already normalized);
    things Roblox draws at a fixed pixel size (E-key prompts, toasts, some pop-ups) are 1/ui_scale as big as
    their template once the screen is shrunk, so that size is tried too."""
    sc = ui_scale()
    if abs(sc - 1.0) < 0.01:
        return (1.0,)
    # The game's own UI (HUD, hotbar, Units panel) does NOT grow as fast as the window: from 1280x720 to 1920x1080
    # it is only ~1.18x bigger, not 1.5x. After the screen is shrunk by 1/sc it is therefore ui_ratio() of its
    # 1280x720 size. That is why pictures made at 1280x720 stopped matching (and the wave counter couldn't be read).
    return (1.0, 1.0 / sc, ui_ratio(sc))


def ui_ratio(sc=None):
    """Size of the game's UI on the virtual 1280-wide screen, relative to its size in a 1280x720 window."""
    sc = ui_scale() if sc is None else sc
    return (1.0 + 0.36 * (sc - 1.0)) / sc


def _factors_for(name):
    out = list(size_factors())
    h = _scale_hint.get(name)
    if h is not None:
        if all(abs(h - o) > 0.03 for o in out):
            out.insert(0, h)
        else:
            out.sort(key=lambda f: abs(math.log(f / h)))      # the size that worked last time goes first
    return out


def _sized(name, f, gray=False):
    key = (name, round(f, 3), gray)
    if key not in _variant_cache:
        tpl = template(name)
        if gray:
            tpl = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        if abs(f - 1.0) > 0.01:
            tpl = cv2.resize(tpl, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
        _variant_cache[key] = tpl
    return _variant_cache[key]


def _best_variant(name, screen):
    """(template, match map) for whichever template size fits the screen best."""
    best = None
    for f in _factors_for(name):
        tpl = _sized(name, f)
        if tpl.shape[0] >= screen.shape[0] or tpl.shape[1] >= screen.shape[1]:
            continue
        res = match(screen, tpl)
        peak = float(res.max())
        if best is None or peak > best[0]:
            best = (peak, tpl, res)
        if _LOCK["on"] and name in _scale_hint and name not in _hint_stale and peak >= LOCK_EARLY_SCORE:
            break                          # matched well at its remembered size: don't try the others
    if (best is None or best[0] < THRESHOLD) and (name not in _scale_hint or name in _hint_stale) \
            and abs(ui_scale() - 1.0) >= 0.01 \
            and not _sweep_off[0] and time.time() - _sweep_last.get(name, 0) > 10:
        # not found at the usual sizes: search other sizes (at most once per 10 s per picture, so pictures that are
        # simply not on screen don't cost anything). If it turns up, its size is remembered and used from now on.
        _sweep_last[name] = time.time()
        score, f, centre = sweep_find(name, screen)
        if centre is not None and score >= max(THRESHOLD, SWEEP_ACCEPT):
            tpl = _sized(name, f)
            return tpl, match(screen, tpl)
    if best is None:                       # template bigger than the screen: let the old path report it
        tpl = template(name)
        return tpl, match(screen, tpl)
    return best[1], best[2]


def best_score(name, screen=None):
    """Highest match score for a template at any size we try (for log messages when something isn't found)."""
    screen = screen if screen is not None else screenshot()
    try:
        return sweep_find(name, screen, 2.0)[0]     # threshold 2.0 = never stop early, never remember
    except Exception:
        return 0.0


_sweep_last = {}          # template name -> when an automatic size search last ran for it
_sweep_off = [0]          # >0 while looking for things that are normally NOT on screen (pop-ups, disconnect)
SWEEP_MIN_SIDE = 20       # never size-search a picture that would be smaller than this (tiny pictures match anything)
SWEEP_ACCEPT = 0.88       # a size found by searching must score at least this to be believed


@contextmanager
def no_sweep():
    """Pictures looked for inside this block are only tried at the normal sizes. Pop-up and disconnect checks run
    all the time for things that are usually absent, so a size search there could only find false matches."""
    _sweep_off[0] += 1
    try:
        yield
    finally:
        _sweep_off[0] -= 1
_half_cache = [None, None]


def _half_gray(screen):
    if _half_cache[0] is not screen:
        _half_cache[1] = cv2.resize(gray_of(screen), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        _half_cache[0] = screen
    return _half_cache[1]


def sweep_find(name, screen=None, threshold=None):
    """Find a picture whatever size it is drawn at. Returns (score, factor, (cx, cy)) for the best size.
    Cheap first pass: ~35 sizes in grey on a half-size screen; then the best two are checked in colour at full
    size. The size that worked is remembered (_scale_hint) so later searches go straight to it."""
    threshold = THRESHOLD if threshold is None else threshold
    screen = screen if screen is not None else screenshot()
    half, base_gray = _half_gray(screen), cv2.cvtColor(template(name), cv2.COLOR_BGR2GRAY)
    prefer = [math.log(f) for f in size_factors()]
    grid = sorted((round(0.45 * 1.07 ** i, 3) for i in range(0, 36)),       # 0.45x ... ~5x in 7% steps
                  key=lambda f: min(abs(math.log(f) - p) for p in prefer))   # nearest the likely sizes first
    coarse = []
    for f in [x for x in grid if x <= 2.4]:
        t = cv2.resize(base_gray, None, fx=f * 0.5, fy=f * 0.5, interpolation=cv2.INTER_AREA)
        if min(t.shape[:2]) < SWEEP_MIN_SIDE // 2 or t.shape[0] >= half.shape[0] or t.shape[1] >= half.shape[1]:
            continue
        coarse.append((float(cv2.minMaxLoc(match(half, t))[1]), f))
    coarse.sort(reverse=True)
    best = (0.0, 1.0, None)
    tried = set()
    for _, f0 in coarse[:2]:
        for f in (f0 * 0.95, f0, f0 * 1.05):        # refine in colour at full size
            f = round(f, 3)
            if f in tried:
                continue
            tried.add(f)
            tpl = _sized(name, f)
            if min(tpl.shape[:2]) < SWEEP_MIN_SIDE or tpl.shape[0] >= screen.shape[0] or tpl.shape[1] >= screen.shape[1]:
                continue
            _, score, _, loc = cv2.minMaxLoc(match(screen, tpl))
            if score > best[0]:
                best = (float(score), f, (int(loc[0] + tpl.shape[1] // 2), int(loc[1] + tpl.shape[0] // 2)))
    if best[2] is not None and best[0] >= max(threshold, SWEEP_ACCEPT):
        changed = abs(best[1] - _scale_hint.get(name, 0)) > 0.03
        if changed:
            log(f"Found {name}.png at {best[1]:.2f}x its saved size (score {best[0]:.2f}); using that size from now on.")
        _scale_hint[name] = best[1]
        _hint_stale.discard(name)
        if changed:
            _save_hints()
    return best


def find_all(name, screen=None, threshold=None):
    """Return centers [(x, y, score)] of every match, duplicates merged."""
    threshold = THRESHOLD if threshold is None else threshold
    screen = screen if screen is not None else screenshot()
    tpl, result = _best_variant(name, screen)
    h, w = tpl.shape[:2]
    ys, xs = np.where(result >= threshold)
    hits = sorted(zip(xs, ys), key=lambda p: -result[p[1], p[0]])
    kept = []
    for x, y in hits:
        if all(abs(x - kx) > w // 2 or abs(y - ky) > h // 2 for kx, ky, _ in kept):
            kept.append((int(x), int(y), float(result[y, x])))
    return [(x + w // 2, y + h // 2, s) for x, y, s in kept]


MULTISCALE = {"go_home", "yes", "back", "next_btn", "open_lich", "open_lich_portal", "lich_enter",
              "none_left", "your_items", "lobby", "lobby_2", "return_hub", "portal_play", "portal_start",
              "daily_title", "daily_claim", "daily_close", "act_play_again", "awesome", "return_home",
              "play_again", "play_again_2", "act_play_again_2"}  # pop-ups animate in, so their size can vary a little
SCALES = (1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15, 1.2, 0.8, 1.25)


def _scale_list(n=None):
    """SCALES around 1.0 and, when auto-scale is on, around the fixed-pixel size too (no repeats)."""
    base = SCALES[:n] if n else SCALES
    out = list(base)
    for f in size_factors()[1:]:
        for x in base:
            v = round(f * x, 3)
            if all(abs(v - o) > 0.02 for o in out):
                out.append(v)
    return out


# Some buttons are drawn bigger in certain situations (hovered by the mouse, selected, or on an end screen with
# no Next button), so one picture can't cover them. Two fixes:
#  * VARIANTS: extra pictures of the same thing. Save them as <name>_1.png, <name>_2.png ... (or one of the
#    aliases below, e.g. play_again_2.png is another look of act_play_again) and they are all tried.
#  * WIDE_SIZE: these pictures are searched over a much wider range of sizes (0.75x ... 1.5x).
VARIANT_ALIASES = {
    "act_play_again": ("play_again", "act_play_again_2", "play_again_2", "play_again_3"),
    "awesome": ("awesome_1", "awesome_2", "awesome_3"),
    "return_hub": ("return_hub_1", "return_hub_2", "return_hub_3", "return_home", "return_home_2"),
}
WIDE_SIZE = {"return_hub", "return_home", "act_play_again", "play_again", "play_again_2", "play_again_3",
             "act_play_again_2", "awesome", "awesome_1", "awesome_2", "awesome_3"}
WIDE_SCALES = (1.0, 1.08, 0.94, 1.16, 0.88, 1.25, 1.35, 0.8, 1.45, 0.75)
_variant_names = {}


def variant_names(name):
    """`name` plus every extra picture of it that exists (name_1.png, name_2.png ... and the aliases), in order.
    Only names that have a file are returned, so a missing alias costs nothing."""
    if name not in VARIANT_ALIASES and not re.search(r"_\d+$", name) and name not in WIDE_SIZE:
        return [name]
    key = (name, res_folder())
    if key not in _variant_names:
        cand = [name] + list(VARIANT_ALIASES.get(name, ())) + [f"{name}_{i}" for i in range(1, 6)]
        seen, out = set(), []
        for n in cand:
            if n in seen:
                continue
            seen.add(n)
            if n == name or template(n, required=False) is not None:
                out.append(n)
        _variant_names[key] = out
    return _variant_names[key]


def has_template(name):
    """True if `name` or one of its variants has a picture."""
    return any(template(n, required=False) is not None for n in variant_names(name))


def find_wide(name, screen=None, threshold=None, scales=WIDE_SCALES):
    """Grayscale match over a wide range of sizes. Returns (score, centre) of the best one, whatever the score."""
    screen = screen if screen is not None else screenshot()
    gray = gray_of(screen)
    best = (0.0, None)
    for sc in scales:
        t = _sized(name, sc, gray=True)
        if t.shape[0] >= gray.shape[0] or t.shape[1] >= gray.shape[1]:
            continue
        _, score, _, loc = cv2.minMaxLoc(match(gray, t))
        if score > best[0]:
            best = (float(score), (int(loc[0] + t.shape[1] // 2), int(loc[1] + t.shape[0] // 2)))
        if score >= 0.95:
            break
    return best


def find_ms(name, screen=None, threshold=None):
    """Grayscale match that tries several sizes; returns the center of the best match."""
    threshold = THRESHOLD if threshold is None else threshold
    screen = screen if screen is not None else screenshot()
    if name in WIDE_SIZE:
        score, pos = find_wide(name, screen, threshold)
        return pos if score >= threshold else None
    gray = gray_of(screen)
    best = (0.0, None)
    for sc in _scale_list(5):
        t = _sized(name, sc, gray=True)       # cached: resized once per size instead of on every look
        if t.shape[0] >= gray.shape[0] or t.shape[1] >= gray.shape[1]:
            continue
        _, score, _, loc = cv2.minMaxLoc(match(gray, t))
        if score > best[0]:
            best = (float(score), (int(loc[0] + t.shape[1] // 2), int(loc[1] + t.shape[0] // 2)))
        if score >= 0.95:
            break
    return best[1] if best[0] >= threshold else None


def find(name, screen=None, threshold=None):
    names = variant_names(name)
    if len(names) > 1:                                     # several pictures of the same thing: first one that matches
        for n in names:
            if template(n, required=False) is None:
                continue
            pos = _find_one(n, screen, threshold)
            if pos:
                return pos
        return None
    return _find_one(name, screen, threshold)


def _find_one(name, screen=None, threshold=None):
    if name in MULTISCALE or name in WIDE_SIZE:
        return find_ms(name, screen, threshold)
    hits = find_all(name, screen, threshold)
    return max(hits, key=lambda h: h[2])[:2] if hits else None


def match_card(name, screen=None):
    """Find an area's card (icon + title) in the Areas menu, grayscale, several sizes.
    Every card has an identical Go button, so we match the icon + title instead.
    Returns (score, go_button_position)."""
    screen = screen if screen is not None else screenshot()
    gray = gray_of(screen)
    tpl = cv2.cvtColor(template(f"card_{name}"), cv2.COLOR_BGR2GRAY)
    best = (0.0, None)
    for sc in _scale_list():
        t = cv2.resize(tpl, None, fx=sc, fy=sc,
                       interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
        if t.shape[0] >= gray.shape[0] or t.shape[1] >= gray.shape[1]:
            continue
        _, score, _, loc = cv2.minMaxLoc(match(gray, t))
        if score > best[0]:
            best = (float(score), (int(loc[0] + CARD_GO_OFFSET[0] * sc),
                                   int(loc[1] + CARD_GO_OFFSET[1] * sc)))
        if score >= 0.9:
            break
    return best


def find_card(name, screen=None):
    score, pos = match_card(name, screen)
    return pos if score >= 0.8 else None


def save_debug(tag):
    """Save what the macro sees so a failure can be diagnosed."""
    DEBUG.mkdir(exist_ok=True)
    path = DEBUG / f"{tag}_{time.strftime('%H%M%S')}.png"
    cv2.imwrite(str(path), screenshot())
    for old in sorted(DEBUG.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-MAX_DEBUG_PICS]:
        try:
            old.unlink()  # keep only the newest few so this folder can't grow forever
        except OSError:
            pass
    log(f"Saved a screenshot of what I see to {path}")


def roblox_crop(screen):
    """The part of a full-screen frame that is the Roblox window (and where its top-left corner is)."""
    rect = roblox_rect()
    if rect:
        l, t, r, b = (max(0, int(v)) for v in rect)
        crop = screen[t:b, l:r]
        if crop.size:
            return crop, (l, t)
    return screen, (0, 0)


def write_image(path, img, ext=".jpg", quality=65):
    """cv2.imwrite can't handle some non-ASCII paths on Windows; encode and write the bytes ourselves."""
    ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(buf.tobytes())
    os.replace(tmp, path)                  # replaced in one step, so a reader never sees half a file
    return path


def make_snapshot(path=None, max_w=960):
    """What the game looks like right now, as a small JPEG for BMO's alerts and the remote dashboard."""
    crop, _ = roblox_crop(screenshot())
    h, w = crop.shape[:2]
    if w > max_w:
        crop = cv2.resize(crop, (max_w, max(1, int(h * max_w / w))), interpolation=cv2.INTER_AREA)
    return write_image(path or LIVE_SHOT, crop)


def do_snapshot():
    p = make_snapshot()
    if p:
        event("snapshot", path=str(p), t=round(time.time(), 1))


def find_wave_button(screen):
    """The Start Wave / Skip Wave button is a big, strongly pink bar in the bottom right corner when it
    can be used and grey when it can't, so finding it means it's pressable. Pink scenery (cotton candy,
    frosting) can look similar, so we take the candidate nearest the corner and check it's solidly pink."""
    h, w = screen.shape[:2]
    rect = roblox_rect()  # the button sits in the bottom-right of the Roblox WINDOW, wherever that is on screen
    if rect:
        min_right = rect[0] + 0.8 * (rect[2] - rect[0])
        min_bottom = rect[1] + 0.8 * (rect[3] - rect[1])
        max_right, max_bottom = rect[2] + 60, rect[3] + 60
    else:
        min_right, min_bottom, max_right, max_bottom = w * 0.3, h * 0.3, w, h
    hsv = hsv_of(screen)
    mask = cv2.inRange(hsv, (150, 112, 190), (179, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 9)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        right, bottom = x + bw, y + bh
        # the button hugs the bottom-right corner; a taller/wider blob can be the button merged with
        # pink scenery, which is fine because only its right and bottom edges are used below
        if not (200 <= bw <= 420 and 40 <= bh <= 175 and bw >= 1.6 * 40
                and min_right <= right <= max_right and min_bottom <= bottom <= max_bottom):
            continue
        if best is None or right + bottom > best[0] + best[1]:  # nearest the corner wins
            best = (right, bottom)
    # the saturated part stops short of the button's pale top edge, so measure from the right/bottom edges
    return (best[0] - 133, best[1] - 33) if best else None


# ---------------- pop-up handlers ("interrupts") ----------------
# Pop-ups arrive at random moments (a unit drop, the daily reward, an event notice). Instead of every
# step having to expect them, the polling loops call handle_interrupts() and anything on this list gets
# dismissed whatever the macro is doing. Add your own in routines/_interrupts.json:
#   [{"name": "event notice", "template": "event_ok", "threshold": 0.85, "cooldown": 5,
#     "action": "click", "dx": 0, "dy": 0}]
# action: "click" (default), "key" (with "key": "esc"), or "daily" (the Daily Rewards routine).
# A pop-up is only handled if its picture exists in templates/ (a missing one is skipped silently).
BUILTIN_INTERRUPTS = [
    {"name": "unit drop (AWESOME!)", "template": "awesome", "threshold": 0.8, "cooldown": 3, "action": "click"},
    {"name": "Daily Rewards", "template": "daily_title", "threshold": 0.8, "cooldown": 20, "action": "daily"},
]
_interrupt_state = {"last": {}, "busy": False, "list": None}


def load_interrupts():
    """Built-in pop-ups plus routines/_interrupts.json. Bad entries are reported once and skipped."""
    out = [dict(i) for i in BUILTIN_INTERRUPTS]
    try:
        raw = json.loads(INTERRUPTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = []
    except Exception as e:
        log(f"Couldn't read routines/_interrupts.json: {e}")
        raw = []
    for i, item in enumerate(raw if isinstance(raw, list) else [], 1):
        if isinstance(item, dict) and item.get("template"):
            item.setdefault("name", str(item["template"]))
            out.append(item)
        else:
            log(f"routines/_interrupts.json entry {i} needs a 'template'; skipped.")
    return [i for i in out if i.get("enabled", True)]


def handle_interrupts(screen=None):
    """Dismiss any known pop-up that is on screen. Returns True if one was handled (so the caller can
    take a fresh screenshot). Cheap when nothing is there: each pop-up is looked for at most every
    INTERRUPT_EVERY seconds."""
    if _interrupt_state["busy"]:
        return False
    if _interrupt_state["list"] is None:
        _interrupt_state["list"] = load_interrupts()
    now = time.monotonic()
    for it in _interrupt_state["list"]:
        name = it["name"]
        last = _interrupt_state["last"].get(name, (-1e9, -1e9))   # (last looked, last handled)
        if now - last[0] < INTERRUPT_EVERY or now - last[1] < float(it.get("cooldown", 5)):
            continue
        _interrupt_state["last"][name] = (now, last[1])
        if not has_template(str(it["template"])):
            continue
        screen = screen if screen is not None else screenshot()
        with no_sweep():
            pos = find(str(it["template"]), screen, float(it.get("threshold", 0.85)))
        if not pos:
            continue
        _interrupt_state["busy"] = True
        try:
            log(f"Pop-up on screen: {name}. Dismissing it.")
            action = it.get("action", "click")
            if action == "daily":
                dismiss_daily_rewards()
            elif action == "key":
                press(str(it.get("key", "esc")))
            else:
                click(pos[0] + int(it.get("dx", 0)), pos[1] + int(it.get("dy", 0)))
            _interrupt_state["last"][name] = (time.monotonic(), time.monotonic())
        finally:
            _interrupt_state["busy"] = False
        return True
    return False


def clear_popups(max_rounds=4):
    """Dismiss known pop-ups (the AWESOME! unit drop...) that are on screen right now, looking at a fresh frame each time.
    The end-of-raid screens used to wait for their buttons without ever looking for a pop-up that was covering them."""
    cleared = 0
    for _ in range(max_rounds):
        control_checkpoint()
        if not handle_interrupts(screenshot()):
            break
        cleared += 1
        sleep(0.6)
    return cleared


def popup_on_screen(screen):
    """True if one of the known pop-ups is in this frame (looked at no more often than INTERRUPT_EVERY)."""
    if _interrupt_state["list"] is None:
        _interrupt_state["list"] = load_interrupts()
    now = time.monotonic()
    for it in _interrupt_state["list"]:
        last = _interrupt_state["last"].get(it["name"], (-1e9, -1e9))
        if now - last[0] < INTERRUPT_EVERY or now - last[1] < float(it.get("cooldown", 5)):
            continue
        if not has_template(str(it["template"])):
            continue
        with no_sweep():
            seen = find(str(it["template"]), screen, float(it.get("threshold", 0.85)))
        if seen:
            return True
        _interrupt_state["last"][it["name"]] = (now, last[1])
    return False


# ---------------- frozen-game detection ----------------
_freeze = {"prev": None, "since": None, "t": 0.0}


def freeze_reset():
    _freeze.update(prev=None, since=None, t=0.0)


def freeze_check(screen):
    """Call once per match-loop tick. A live match always moves a little (enemies, timers, the wave
    counter); a picture that is identical for FREEZE_SECS means the game is hung. Raises Frozen."""
    if FREEZE_SECS <= 0:
        return
    now = time.monotonic()
    if now - _freeze["t"] < 2.0:
        return
    if _freeze["t"] and now - _freeze["t"] > 15:   # we were paused / waiting for focus: start counting again
        freeze_reset()
    _freeze["t"] = now
    crop, _ = roblox_crop(screen)
    small = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (96, 54), interpolation=cv2.INTER_AREA)
    prev = _freeze["prev"]
    _freeze["prev"] = small
    if prev is None or float(cv2.absdiff(prev, small).mean()) > 0.5:
        _freeze["since"] = now
        return
    if _freeze["since"] is not None and now - _freeze["since"] >= FREEZE_SECS:
        freeze_reset()
        raise Frozen


def wait_until(finder, timeout=None):
    """Poll finder() until it returns something. Each tick takes ONE screenshot that the finder and the
    disconnect check share (it used to take two)."""
    timeout = MATCH_TIMEOUT if timeout is None else timeout
    end = time.time() + timeout
    while time.time() < end:
        control_checkpoint()
        frame = _pinned[0] if _pinned[0] is not None else screenshot()
        with pinned_frame(frame):
            pos = finder()
            if pos:
                return pos
            check_disconnected()
            popup = popup_on_screen(frame)
        if popup and handle_interrupts():   # outside the pinned block: dismissing needs fresh screenshots
            end += 3                        # time spent dismissing a pop-up shouldn't use up the step's wait
        sleep(0.25)
    return None


def wait_for(name, timeout=None, threshold=None):
    started = time.time()

    def finder():
        pos = find(name, threshold=threshold)
        if pos is None and time.time() - started > 3 and name not in MULTISCALE:
            score, _, where = sweep_find(name, None, threshold)   # maybe it is drawn at another size on this screen
            thr = max(SWEEP_ACCEPT, THRESHOLD if threshold is None else threshold)
            if where is not None and score >= thr:
                pos = where
        return pos
    return wait_until(finder, timeout)


# ---------------- input ----------------
def click(x, y, hover=0.08, hold=0.07):
    x, y = int(x), int(y)
    # Roblox only registers a button when the cursor MOVES onto it, so approach from a distance
    pydirectinput.moveTo(x - 40 if x > 60 else x + 40, y - 30 if y > 60 else y + 30)
    sleep(0.05)
    pydirectinput.moveTo(x, y)
    sleep(hover)
    pydirectinput.moveTo(x + 1, y + 1)
    sleep(0.05)
    if hold:
        pydirectinput.mouseDown()
        try:
            time.sleep(hold)  # finish the short click before honoring a pause
        finally:
            pydirectinput.mouseUp()
    else:
        pydirectinput.click()
    mouse_pos[:] = [x, y]
    sleep(0.3)


def drag(x1, y1, x2, y2):
    """Press at (x1, y1), move to (x2, y2) in steps, release."""
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    pydirectinput.moveTo(x1 - 30 if x1 > 60 else x1 + 30, y1 - 20 if y1 > 60 else y1 + 20)
    sleep(0.05)
    pydirectinput.moveTo(x1, y1)
    sleep(0.2)
    pydirectinput.mouseDown()
    try:
        time.sleep(0.15)
        for i in range(1, 11):
            pydirectinput.moveTo(x1 + (x2 - x1) * i // 10, y1 + (y2 - y1) * i // 10)
            time.sleep(0.04)
        time.sleep(0.25)
    finally:
        pydirectinput.mouseUp()
    mouse_pos[:] = [x2, y2]
    sleep(0.3)


def press(key):
    pydirectinput.keyDown(key)
    try:
        time.sleep(0.08)  # release short key presses before pausing
    finally:
        pydirectinput.keyUp(key)
    sleep(0.3)


def focus_roblox():
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getWindowsWithTitle("Roblox") if w.width > 300]
        exact = [w for w in wins if w.title.strip() == "Roblox"]  # not a browser tab or Studio
        win = (exact or wins or [None])[0]
        if win:
            if win.isMinimized:
                win.restore()
            win.activate()
            sleep(0.5)
    except Exception:
        pass  # optional; click on the game once yourself if this fails


# ---------------- daily rewards popup ----------------
def dismiss_daily_rewards(wait=0):
    """The Daily Rewards window covers the hub when you join. Press Claim once, then the X at the
    top right. With wait>0 it keeps looking for that many seconds (it pops up a moment after loading).
    Returns True if it found and closed the window."""
    end = time.time() + wait
    while not find("daily_title", threshold=0.8):
        if time.time() >= end:
            return False
        sleep(0.5)
    log("Daily Rewards window is open: pressing Claim, then the X.")
    claimed = False
    for _ in range(10):
        screen = screenshot()
        if not find("daily_title", screen, threshold=0.8):
            log("Daily Rewards closed.")
            sleep(0.5)
            return True
        claim = None if claimed else find("daily_claim", screen, threshold=0.8)
        if claim:
            click(*claim)
            claimed = True
            sleep(1.5)  # reward animation
            continue
        close = find("daily_close", screen, threshold=0.8)
        if close:
            click(*close)
            sleep(1.0)
        else:
            sleep(1.0)
    log("Couldn't close the Daily Rewards window.")
    save_debug("daily_rewards")
    return False


HUB_STABLE_LOOKS = 2        # hub_ready must be seen this many looks in a row (about a second) before we trust it
HUB_SETTLE = float(os.getenv("CANDY_HUB_SETTLE", "1.0"))        # pause after the hub first shows, for banners to finish
HUB_DAILY_WAIT = float(os.getenv("CANDY_HUB_DAILY_WAIT", "3"))  # how long to watch for the Daily Rewards window after that
HUB_TIMEOUT = 180           # seconds to wait for the hub before the run is counted as failed
_HOME_SCREEN_PICTURES = ("play_button", "game_tile", "favorites_item", "menu_icon")


def in_match_now(screen):
    """True if the screen shows the match HUD (Start Wave, the Units panel or the wave counter)."""
    return bool(find("start_match", screen, 0.75) or find_wave_button(screen) or find("heroes_title", screen)
                or read_wave(screen) is not None or find_upgrade_all(screen, None))


def wait_for_hub(timeout=HUB_TIMEOUT):
    """Block until the hub has REALLY loaded and is calm, then return True (False if it never does).
    Everything that opens an area (M, E, walking) starts with this, so nothing is pressed while the game is
    still loading, teleporting back from a match, or showing the Daily Rewards window.
      - hub_ready must stay visible for several looks in a row (one lucky frame isn't enough);
      - if we had to wait for the load, give the hub a moment to settle and for Daily Rewards to pop up;
      - a Disconnected dialog raises Disconnected (main() rejoins); the Roblox home screen starts recover();
      - if we're still inside a match, go home first."""
    if template("hub_ready", required=False) is None:
        log("templates/hub_ready.png is missing, so I can't tell when the hub has loaded. Waiting 5 seconds instead.")
        sleep(5)
        return True
    activity("Waiting for the hub to load")
    started = time.time()
    seen, waited, in_match, last_note, tried_home = 0, False, 0, started, False
    while time.time() - started < timeout:
        control_checkpoint()
        frame = screenshot()
        daily = hub = match_hud = home = False
        with pinned_frame(frame):
            check_disconnected(frame)
            daily = bool(find("daily_title", frame, 0.8))
            hub = bool(find("hub_ready", frame))
            if not hub and not daily:
                match_hud = in_match_now(frame)
                home = any(find(n, frame, 0.85) for n in _HOME_SCREEN_PICTURES)
        if daily:                                  # the Daily Rewards window covers the hub: clear it first
            dismiss_daily_rewards()
            seen = 0
            continue
        if hub:
            seen += 1
            in_match = 0
            if seen >= HUB_STABLE_LOOKS:
                if waited:                         # we watched it load: let banners finish and Daily Rewards pop up
                    log(f"The hub is showing after {time.time() - started:.0f}s. Letting it settle.")
                    sleep(HUB_SETTLE)
                    dismiss_daily_rewards(wait=HUB_DAILY_WAIT)
                else:
                    sleep(0.3)
                    dismiss_daily_rewards()
                if find("hub_ready"):              # still there after the settle = really ready
                    log(f"Hub ready ({time.time() - started:.0f}s in total).")
                    activity("Hub ready")
                    return True
                seen, waited = 0, True             # something covered it (a banner, a loading screen): keep waiting
                continue
            sleep(0.25)
            continue
        seen = 0
        waited = True
        if match_hud:
            in_match += 1
            if in_match >= 4:
                log("Still inside a match - going back to the hub first.")
                in_match = 0
                if not go_home_from_match():
                    log("Couldn't leave the match.")
                continue
        elif not home and not tried_home and time.time() - started > 40:
            tried_home = True      # nothing recognised for 40s: maybe a match screen we can't read. Use the Lobby button.
            log("Nothing I recognise for 40 seconds - trying the Lobby button in case this is a match.")
            go_home_from_match()
            continue
        elif home and time.time() - started > 20:
            log("This looks like the Roblox home screen, not the game. Rejoining.")
            return bool(recover(max_wait=300))
        if time.time() - last_note > 20:
            last_note = time.time()
            log(f"Still waiting for the hub to load ({time.time() - started:.0f}s, hub_ready best match "
                f"{best_score('hub_ready', frame):.2f}, need {THRESHOLD:.2f}).")
        sleep(0.4)
    log(f"The hub never finished loading in {timeout} seconds (hub_ready best match {best_score('hub_ready'):.2f}).")
    save_debug("hub_wait")
    return False


def prepare():
    """Start of every run: focus Roblox and wait until the hub is loaded (this also clears Daily Rewards).
    Returns False if the hub never showed up."""
    focus_roblox()
    display_report()
    return wait_for_hub()


# ---------------- disconnects ----------------
def check_disconnected(screen=None):
    with no_sweep():
        found = find("reconnect", screen, threshold=0.85)
    if found:
        raise Disconnected


def nudge():
    """Wiggle the mouse a few pixels so Roblox doesn't kick us for being idle."""
    x, y = mouse_pos
    pydirectinput.moveTo(x + 6, y)
    sleep(0.05)
    pydirectinput.moveTo(x, y)


def ai_diagnose(screen, where):
    """Optional and OFF by default (see bmo_assist.py): ask a vision model what screen this is and log the answer.
    It only reports. It never clicks or presses keys. Any problem is swallowed so it can't disturb the macro."""
    try:
        import bmo_assist
        if not bmo_assist.available():
            return None
        crop, _ = roblox_crop(screen)
        h, w = crop.shape[:2]
        if w > 960:
            crop = cv2.resize(crop, (960, max(1, int(h * 960 / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        res = bmo_assist.diagnose(buf.tobytes()) if ok else None
        if res:
            log(f"AI helper ({where}): looks like '{res['label']}'. {res['reason']}")
            event("ai_diagnosis", where=where, label=res["label"], reason=res["reason"])
        return res
    except Exception:
        return None


def recover(max_wait=300):
    """Get back into the game: Reconnect, or Roblox home -> menu -> Favorites -> game -> Play."""
    log("Trying to get back into the game...")
    started, diagnosed = time.time(), False
    steps = [  # deepest screen first, so we always click the furthest step we can see
        ("reconnect", 0.85), ("play_button", 0.85), ("game_tile", 0.85),
        ("favorites_item", 0.85), ("menu_icon", 0.85),
    ]
    last_click = {}
    played_at = None
    end = time.time() + max_wait
    while time.time() < end:
        control_checkpoint()
        screen = screenshot()
        if find("daily_title", screen, threshold=0.8):
            dismiss_daily_rewards()
            continue
        if find("hub_ready", screen):
            log("Back in the hub.")
            sleep(2)
            dismiss_daily_rewards(wait=6)  # it pops up a few seconds after the hub loads
            return True
        if played_at and time.time() - played_at > 90:
            log("Assuming the game has loaded.")
            dismiss_daily_rewards(wait=6)
            return True
        for name, th in steps:
            pos = find(name, screen, threshold=th)
            if pos and time.time() - last_click.get(name, 0) > 15:
                log(f"Clicking {name}.")
                click(*pos)
                last_click[name] = time.time()
                if name == "play_button":
                    played_at = time.time()
                break
        if not diagnosed and time.time() - max([started, *last_click.values()]) > 120:
            diagnosed = True      # nothing recognised for 2 minutes: let the optional AI helper say what it sees
            ai_diagnose(screen, "recover")
        sleep(2)
    return False


# ---------------- maps + unit placement ----------------
def detect_map(screen, play_x):
    """Which map label (Candy/Ice Kingdom) sits on the car whose Play button is at play_x."""
    best, best_dist = None, 150
    for name in MAPS:
        for x, _, _ in find_all(f"map_{name}", screen, threshold=0.85):
            if abs(x - play_x) < best_dist:
                best, best_dist = name, abs(x - play_x)
    return best


def record_placements(anchor):
    """Record the keys, clicks, drags and WALKING (W/A/S/D) the player makes until the record key (F8 by default) is
    pressed. A walk is saved as one "move" event with how long each key was held; a picture of the screen after each
    walk is saved next to the placements so the replay can tell where the character ended up."""
    events, last, down = [], [None], {}
    tracker = common.MoveTracker()
    pending = []                     # move events still waiting for their "after the walk" picture: [(event, due)]
    views = [0]

    def stamp(now=None):
        now = time.time() if now is None else now
        dt = 0.0 if last[0] is None else max(0.0, now - last[0])
        last[0] = now
        return round(dt, 3)

    def on_press(key):
        char = getattr(key, "char", None)
        if char and key != PLACEMENT_KEY:      # the key that ends the recording isn't part of it
            if tracker.is_move_key(char):
                if not tracker.down and not tracker.burst:
                    down["move_dt"] = stamp()   # gap since the previous action, measured when the walk begins
                tracker.press(char, time.time())
            else:
                events.append({"dt": stamp(), "type": "key", "key": char})

    def on_release(key):
        char = getattr(key, "char", None)
        if char and tracker.is_move_key(char):
            ev = tracker.release(char, time.time())
            if ev:
                last[0] = ev.pop("end")
                ev.pop("start", None)
                ev["dt"] = down.pop("move_dt", 0.0)
                events.append(ev)
                pending.append((ev, time.time() + 0.7))   # let the camera settle before taking the picture

    def on_click(x, y, button, pressed):
        if button != mouse.Button.left:
            return
        if pressed:
            down["pos"], down["dt"] = (int(x / ui_scale()), int(y / ui_scale())), stamp()
            return
        if "pos" not in down:
            return
        (x1, y1), (x2, y2) = down.pop("pos"), (int(x / ui_scale()), int(y / ui_scale()))
        if abs(x2 - x1) + abs(y2 - y1) > 14:  # pressed on one spot, released on another = a drag
            events.append({"dt": down["dt"], "type": "drag", "dx": x1 - anchor[0], "dy": y1 - anchor[1],
                           "dx2": x2 - anchor[0], "dy2": y2 - anchor[1]})
        else:
            events.append({"dt": down["dt"], "type": "click", "dx": x1 - anchor[0], "dy": y1 - anchor[1]})

    def snapshot(ev):
        views[0] += 1
        try:
            save_move_view(ev, anchor, views[0])
        except Exception as e:            # a missing picture only means less accurate spots after this walk
            log(f"Couldn't save the picture after a walk ({type(e).__name__}: {e}); spots after it will be less exact.")

    kl = keyboard.Listener(on_press=on_press, on_release=on_release)
    ml = mouse.Listener(on_click=on_click)
    kl.start()
    ml.start()
    go_flag.clear()
    try:
        while not go_flag.is_set():
            sleep(0.2)
            while pending and time.time() >= pending[0][1] and not tracker.down:
                snapshot(pending.pop(0)[0])
    finally:
        kl.stop()
        ml.stop()
    for ev, _ in pending:                 # finished right after a walk: take the picture now
        snapshot(ev)
    walks = sum(1 for e in events if e.get("type") == "move")
    if walks:
        log(f"Recorded {walks} walk(s) (W/A/S/D with how long each was held).")
    return events


def move_view_path(index):
    """placements/<map>[_res]_view<N>.png: the screen after the N-th recorded walk."""
    return PLACEMENTS / f"{placement_path(current_map).stem}_view{index}.png"


def save_move_view(ev, anchor, index):
    """After a recorded walk: save how the ground looks now (shifted so the Start Wave button sits at REF_ANCHOR, like
    the refs/ pictures) and how the map's usual spawn view maps onto it, so the replay can follow the character."""
    frame = screenshot()
    view = make_view(current_map, frame, anchor)                       # spawn view -> this screen
    H, W = frame.shape[:2]
    shift = np.float32([[1, 0, REF_ANCHOR[0] - anchor[0]], [0, 1, REF_ANCHOR[1] - anchor[1]]])
    gray = cv2.warpAffine(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), shift, (W, H))
    PLACEMENTS.mkdir(exist_ok=True)
    path = move_view_path(index)
    if not cv2.imwrite(str(path), gray):
        raise OSError(f"couldn't write {path.name}")
    ev["view"] = index
    ev["v"] = [[round(float(x), 5) for x in row] for row in np.asarray(view, dtype=float)]
    ev["anchor"] = [int(anchor[0]), int(anchor[1])]


def placement_path(map_name):
    """placements/<map>.json for a ~1280x720 window, placements/<map>_1920x1080.json for a 1920x1080 one, etc.
    Each resolution keeps its own recording, because Roblox's HUD does not scale the same way as the world."""
    res = capture_res_name()
    return PLACEMENTS / (f"{map_name}_{res}.json" if res else f"{map_name}.json")


COUNTER_OFFSET = (-1035, -24)  # card 1's placed/limit counter ("0/1"), relative to the Start Wave button
HOTBAR_SPACING = 121.4         # distance between hotbar cards


def counter_mask(card, anchor):
    """The light-colored digits of hotbar card 1-6's placed/limit counter, as a 0/1 mask."""
    cx = int(anchor[0] + COUNTER_OFFSET[0] + HOTBAR_SPACING * (card - 1))
    cy = int(anchor[1] + COUNTER_OFFSET[1])
    region = screenshot()[max(0, cy - 13):cy + 13, max(0, cx - 23):cx + 23]
    if region.shape[0] < 26 or region.shape[1] < 46:
        return None
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 1] < 75) & (hsv[..., 2] > 115)).astype(np.uint8)[6:21, 4:44]


def counter_changed(before, after):
    """True if the counter's digits changed (a unit was placed). Works for any unit limit,
    because it only asks whether the count moved, not whether the card is full."""
    best = 0.0
    for dx in (-2, -1, 0, 1, 2):
        for dy in (-1, 0, 1):
            moved = np.roll(np.roll(after, dx, axis=1), dy, axis=0)
            best = max(best, float((before & moved).sum() / max(1, (before | moved).sum())))
    return best < 0.85


def wall_probe(frame):
    """A small grayscale picture of the ground (not the HUD, and not the character in the middle of the screen)."""
    H, W = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[int(H * 0.24):int(H * 0.65), :]
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    small[int(h * 0.1):int(h * 0.9), int(w * 0.33):int(w * 0.67)] = 0      # the character stays near the middle
    return small


def walk_move(ev):
    """Replay one recorded walk: hold each key for as long as it was held (keys held together stay together). When
    the character walks into a wall (the screen was scrolling, then stops) the keys are let go early."""
    keys = common.move_keys_of(ev)
    if not keys:
        return
    sleep(min(max(float(ev.get("dt", 0.0)), 0.3), 2.0))
    total = max(at + hold for _, at, hold in keys)
    state = [0] * len(keys)                          # 0 = waiting, 1 = held, 2 = let go
    wall = common.WallDetector(WALL_STALL, WALL_MOTION, WALL_STILL)
    prev, started, hit = None, time.time(), False
    try:
        while True:
            control_checkpoint()
            t = time.time() - started
            for i, (k, at, hold) in enumerate(keys):
                if state[i] == 0 and t >= at:
                    pydirectinput.keyDown(k)
                    state[i] = 1
                if state[i] == 1 and t >= at + hold:
                    pydirectinput.keyUp(k)
                    state[i] = 2
            if t >= total:
                break
            if WALL_STOP and total > 0.6:
                cur = wall_probe(screenshot())
                if prev is not None and prev.shape == cur.shape:
                    diff = float(np.mean(cv2.absdiff(prev, cur)))
                    if wall.update(diff, time.time()):
                        hit = True
                        break
                prev = cur
            time.sleep(0.03)
    finally:
        for i, (k, _, _) in enumerate(keys):
            if state[i] == 1:
                pydirectinput.keyUp(k)
    names = "+".join(k for k, _, _ in keys)
    if hit:
        log(f"Walked {names}: hit a wall after {time.time() - started:.1f}s of {total:.1f}s, so I stopped there.")
    else:
        log(f"Walked {names} for {total:.1f}s.")
    sleep(0.5)                                       # let the camera settle


def view_after_move(ev, anchor, zones, roads):
    """After a replayed walk: work out where the screen is now compared with when the walk was recorded.
    Returns (spots_view, zones_view, zones, roads): `spots_view` moves the spots you recorded after this walk,
    `zones_view` moves the zone lines and roads (which are drawn for the spawn view). If the picture saved with the
    walk is missing, the zones can't be related to the new view, so they are not used until the next walk."""
    base = np.float32([[1, 0, anchor[0] - REF_ANCHOR[0]], [0, 1, anchor[1] - REF_ANCHOR[1]]])
    idx = ev.get("view")
    ref = move_view_path(idx) if idx else None
    if not ref or not ref.exists():
        log("No picture was saved with this walk; placing the spots after it where you clicked them.")
        return base, base, [], []
    try:
        live = make_view(current_map, screenshot(), anchor, ref_path=ref)      # recorded screen -> this screen
        a0, v = ev.get("anchor"), ev.get("v")
        if not a0 or not v:
            return live, live, [], []
        shift = [[1, 0, REF_ANCHOR[0] - a0[0]], [0, 1, REF_ANCHOR[1] - a0[1]]]
        zone_view = np.float32(common.compose_affine(live, shift, v))
    except Exception as e:
        log(f"Couldn't follow the walk ({type(e).__name__}: {e}); placing the spots after it where you clicked them.")
        return base, base, [], []
    off = math.hypot(*(np.array(to_live(live, 0, 0)) - np.array(anchor)))
    if off > 6:
        log(f"After the walk the screen is {off:.0f}px away from where it was when recorded; moving the spots to match.")
    return live, zone_view, zones, roads


def play_events(events, anchor):
    for ev in events:
        if ev.get("type") == "move":
            walk_move(ev)
            continue
        sleep(min(max(ev["dt"], 0.3), 2.0))
        if ev["type"] == "key":
            press(ev["key"])
        elif ev["type"] == "drag":
            drag(anchor[0] + ev["dx"], anchor[1] + ev["dy"], anchor[0] + ev["dx2"], anchor[1] + ev["dy2"])
        else:
            click(anchor[0] + ev["dx"], anchor[1] + ev["dy"], hover=0.25, hold=0.08)


# ---------------- smart placement: hover, read the game's message, click only if valid ----------------
def load_zone_data():
    try:
        data = json.loads(ZONES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}
    zones = {k: v for k, v in data.items() if k != "roads" and not k.startswith("_")}
    return zones, data.get("roads", {})


ZONES, ROADS = load_zone_data()


def make_view(map_name, screen, anchor, ref_path=None):
    """Work out how the screen differs from the usual spawn view of this map (the character walked
    off, the camera got nudged...) so zones and roads can follow it. Returns a 2x3 matrix taking a
    point of the usual view to its place on screen now. Falls back to "same view, shifted to the
    Start Wave button" when the map can't be matched."""
    dx, dy = anchor[0] - REF_ANCHOR[0], anchor[1] - REF_ANCHOR[1]
    base = np.float32([[1, 0, dx], [0, 1, dy]])
    ref_path = Path(ref_path) if ref_path else REFS / f"{map_name}.png"
    if not ref_path.exists():
        return base
    ref = cv2.imread(str(ref_path), cv2.IMREAD_GRAYSCALE)
    H, W = screen.shape[:2]
    ref_live = cv2.warpAffine(ref, base, (W, H))
    live = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    mask = np.zeros((H, W), np.uint8)       # look at the ground only: not the HUD, hotbar or your character
    mask[max(0, 170 + dy):max(0, min(H, 470 + dy)), max(0, dx):max(0, min(W, 1279 + dx))] = 255
    mask[max(0, 200 + dy):max(0, 470 + dy), max(0, 440 + dx):max(0, 830 + dx)] = 0
    orb = cv2.ORB_create(2000, fastThreshold=8)
    k1, d1 = orb.detectAndCompute(ref_live, mask)
    k2, d2 = orb.detectAndCompute(live, mask)
    if d1 is None or d2 is None or len(k1) < 12 or len(k2) < 12:
        return base
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1, d2, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 10:
        return base
    src = np.float32([k1[m.queryIdx].pt for m in good])
    dst = np.float32([k2[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if M is None or int(inl.sum()) < 10 or inl.sum() < 0.4 * len(good):
        return base
    scale = math.hypot(M[0, 0], M[1, 0])
    if not (0.85 <= scale <= 1.18) or abs(math.degrees(math.atan2(M[1, 0], M[0, 0]))) > 12:
        return base
    full = np.vstack([M, [0, 0, 1]]) @ np.vstack([base, [0, 0, 1]])
    return np.float32(full[:2])


def to_live(view, ox, oy):
    """A point given relative to the Start Wave button on the usual view -> where it is on screen now."""
    x, y = REF_ANCHOR[0] + ox, REF_ANCHOR[1] + oy
    return (view[0, 0] * x + view[0, 1] * y + view[0, 2], view[1, 0] * x + view[1, 1] * y + view[1, 2])


def near_road(pt, road_lines):
    """True if pt is on a road (or within ROAD_MARGIN of its edge). road_lines: lists of (x, y, half_width)."""
    for line in road_lines:
        for (x1, y1, h1), (x2, y2, h2) in zip(line, line[1:]):
            dx, dy = x2 - x1, y2 - y1
            t = max(0.0, min(1.0, ((pt[0] - x1) * dx + (pt[1] - y1) * dy) / (dx * dx + dy * dy or 1.0)))
            if math.hypot(pt[0] - (x1 + t * dx), pt[1] - (y1 + t * dy)) <= h1 + (h2 - h1) * t + ROAD_MARGIN:
                return True
    return False


def read_toast(screen, cursor):
    """While you're placing a unit the game shows a small purple message near the cursor:
    a green check ("Click to place ...") when the spot is fine, a red X ("Too close to the edge",
    "Not on the road", "Too close to another unit", "Something is in the way") when it isn't.
    We look for that little icon (templates/toast_ok.png and toast_bad.png).
    Returns "ok", "bad", or None when no message is showing."""
    h, w = screen.shape[:2]
    x0, x1 = max(0, cursor[0] - 330), min(w, cursor[0] + 330)
    y0, y1 = max(0, cursor[1] - 150), min(h, cursor[1] + 230)
    region = screen[y0:y1, x0:x1]
    scores = {}
    for name in ("ok", "bad"):
        best = 0.0
        for f in size_factors():
            tpl = _sized(f"toast_{name}", f)
            if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
                continue
            best = max(best, float(cv2.minMaxLoc(match(region, tpl))[1]))
        if not best:
            return None
        scores[name] = best
    best = max(scores, key=scores.get)
    return best if scores[best] >= TOAST_THRESHOLD else None


def hover(x, y):
    """Move the cursor onto a spot and report what the game says about it ("ok"/"bad"/None).
    Roblox only refreshes the placement preview when the mouse moves, so we always approach."""
    x, y = int(x), int(y)
    pydirectinput.moveTo(x - 25 if x > 60 else x + 25, y - 18 if y > 60 else y + 18)
    sleep(0.04)
    pydirectinput.moveTo(x, y)
    sleep(0.04)
    pydirectinput.moveTo(x + 1, y + 1)
    mouse_pos[:] = [x, y]
    sleep(HOVER_WAIT * max(1.0, min(1.6, 0.6 + 0.4 * ui_scale())))   # bigger screens draw the message a little later
    first = read_toast(screenshot(), (x, y))
    sleep(0.08)
    second = read_toast(screenshot(), (x, y))
    if first == second:
        return second
    sleep(0.15)  # the message was still changing; take a third look
    return read_toast(screenshot(), (x, y))


# ---------------- bad-spot memory ----------------
# Spots the game keeps refusing (a decoration, an invisible wall) cost a hover each, every run. We note
# each refusal against the map; after BAD_SPOT_STRIKES net refusals the spot is skipped. A success
# there takes a strike back, so one unlucky moment (an enemy standing on it) is forgiven.
_bad = {"data": None, "dirty": False}


def _bad_load():
    if _bad["data"] is None:
        try:
            _bad["data"] = json.loads(BAD_SPOTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _bad["data"] = {}
    return _bad["data"]


def _to_ref(view, x, y):
    """Screen point -> the same point on the usual spawn view, relative to the Start Wave button (like zones.json)."""
    inv = cv2.invertAffineTransform(np.float32(view))
    rx = inv[0, 0] * x + inv[0, 1] * y + inv[0, 2]
    ry = inv[1, 0] * x + inv[1, 1] * y + inv[1, 2]
    return float(rx - REF_ANCHOR[0]), float(ry - REF_ANCHOR[1])


def note_spot(view, x, y, ok):
    """Record that a spot was refused (ok=False) or worked (ok=True) on the current map."""
    if not current_map:
        return
    ox, oy = _to_ref(view, x, y)
    spots = _bad_load().setdefault(current_map, [])
    for sp in spots:
        if math.hypot(sp[0] - ox, sp[1] - oy) <= 10:
            sp[2] = max(0, sp[2] - 1) if ok else sp[2] + 1
            _bad["dirty"] = True
            return
    if not ok:
        spots.append([round(ox, 1), round(oy, 1), 1])
        _bad["dirty"] = True


def bad_spots_live(view):
    """Spots to skip on this map, as screen points."""
    if not current_map:
        return []
    return [to_live(view, sp[0], sp[1]) for sp in _bad_load().get(current_map, []) if sp[2] >= BAD_SPOT_STRIKES]


def save_bad_spots():
    if not _bad["dirty"] or _bad["data"] is None:
        return
    try:
        data = {m: [sp for sp in v if sp[2] > 0] for m, v in _bad["data"].items()}
        PLACEMENTS.mkdir(exist_ok=True)
        common.write_json_atomic(BAD_SPOTS_FILE, data, indent=1)
        _bad["dirty"] = False
    except OSError as e:
        log(f"Couldn't save {BAD_SPOTS_FILE.name}: {e}")


def spot_candidates(preferred, zones, roads, view, placed, pview=None):
    """Spots to try for one unit, best first: the spot you recorded (and a few pixels around it),
    then points along your zone lines. Zones/roads follow the camera (view). Anything on the road or
    too near a unit we already placed is skipped without even hovering there."""
    scale = math.hypot(view[0, 0], view[1, 0])
    road_lines = []
    if AVOID_ROAD:
        for line in roads:
            road_lines.append([(*to_live(view, x, y), hw * scale) for x, y, hw in line])

    def far_enough(pt, gap):
        return all(math.hypot(pt[0] - q[0], pt[1] - q[1]) >= gap for q in placed)

    out, seen = [], set()
    avoid = bad_spots_live(view)

    def add(pt, gap, exempt_road=False):
        key = (round(pt[0] / 4), round(pt[1] / 4))
        if key in seen or not far_enough(pt, gap):
            return
        if not exempt_road and any(math.hypot(pt[0] - a[0], pt[1] - a[1]) <= 10 for a in avoid):
            return
        if not exempt_road and road_lines and near_road(pt, road_lines):
            return
        seen.add(key)
        out.append((int(pt[0]), int(pt[1])))

    if preferred:
        px, py = to_live(view if pview is None else pview, *preferred)
        add((px, py), 20, exempt_road=True)   # you placed there yourself, so trust it
        for r in (12, 24):
            for k in range(6):
                a = k * math.pi / 3
                add((px + r * math.cos(a), py + r * math.sin(a)), 20)
    for off in (0, 14, -14, 28, -28):  # the line itself first, then a band to each side of it
        for x1, y1, x2, y2 in zones:
            (x1, y1), (x2, y2) = to_live(view, x1, y1), to_live(view, x2, y2)
            length = math.hypot(x2 - x1, y2 - y1) or 1.0
            n = max(2, int(length // ZONE_STEP) + 1)
            nx, ny = -(y2 - y1) / length, (x2 - x1) / length
            for i in range(n):
                t = i / (n - 1)
                add((x1 + (x2 - x1) * t + nx * off, y1 + (y2 - y1) * t + ny * off), SPOT_GAP)
    return out


def place_unit(key, card, anchor, preferred, zones, roads, view, placed, pview=None):
    """Select a unit, then test spots until the game shows the green check AND the unit really
    lands (its placed-counter moves). Returns the spot used, or None."""
    before = counter_mask(card, anchor) if card else None
    spots = spot_candidates(preferred, zones, roads, view, placed, pview)[:MAX_SPOTS]
    if not spots:
        log(f"Unit {key}: no free spots left in the zones for this map.")
        return None
    press(key)
    no_preview, reselected = 0, False
    for n, (x, y) in enumerate(spots, 1):
        state = hover(x, y)
        if state is None:
            # no preview at all: the key didn't register, or there's no cash / the limit is reached
            no_preview += 1
            if no_preview == 2 and not reselected:
                press(key)  # maybe the first press was missed (or toggled it off): select again
                reselected = True
            elif no_preview >= 5:
                log(f"Unit {key}: no placement preview shows up (not enough cash, or already at its limit).")
                break
            continue
        no_preview = 0
        if state == "bad":
            if not (preferred and n == 1):        # the spot you recorded yourself is never blamed
                note_spot(view, x, y, False)
            continue
        # green check: place it
        pydirectinput.mouseDown()
        sleep(0.08)
        pydirectinput.mouseUp()
        sleep(0.9)
        # The preview disappears once a unit is placed. That is the main proof: the hotbar counter is only read
        # at the 1280x720 layout (at 1920x1080 the cards are a different size/spacing, so it read the wrong
        # place and called good placements failures, then kept trying other spots with the unit already out).
        gone = read_toast(screenshot(), (x, y)) is None
        landed = gone
        if not gone and ui_scale() < 1.2:
            after = counter_mask(card, anchor) if card else None
            if before is not None and after is not None:
                landed = counter_changed(before, after)
        note_spot(view, x, y, bool(landed))
        if landed:
            placed.append((x, y))
            log(f"Unit {key} placed at ({x - anchor[0]}, {y - anchor[1]}) after {n} spot(s).")
            save_bad_spots()
            return (x, y)
        log(f"Unit {key}: clicked ({x - anchor[0]}, {y - anchor[1]}) but the count didn't change; trying another spot.")
    save_bad_spots()
    log(f"Couldn't place unit {key}; gave up after {n} spot(s).")
    save_debug(f"unit{key}")
    press("q")  # leave placement mode so the next unit starts clean
    return None


def drag_card(ev, anchor):
    """Which hotbar card (1-6) a recorded drag started on, or None."""
    dx, dy = ev["dx"], ev["dy"]
    if not (-120 <= dy <= 50):
        return None
    k = round((dx - (COUNTER_OFFSET[0] - 32)) / HOTBAR_SPACING) + 1
    return str(k) if 1 <= k <= 6 else None


def replay_placements(events, anchor):
    zones0 = ZONES.get(current_map, [])
    roads0 = ROADS.get(current_map, [])
    zones, roads = zones0, roads0
    view = make_view(current_map, screenshot(), anchor)
    pview = view                                    # moves the spots you recorded (differs from `view` after a walk)
    shift = math.hypot(*(np.array(to_live(view, 0, 0)) - np.array(anchor)))
    if shift > 6:
        log(f"The view is {shift:.0f}px off the usual spawn view; moving the zones and roads to match.")
    placed = []
    for kind, part in common.split_moves(events):
        if kind == "move":                          # a recorded walk (W/A/S/D): do it, then re-find where we are
            walk_move(part)
            pview, view, zones, roads = view_after_move(part, anchor, zones0, roads0)
            continue
        # a group = one unit hotkey plus the clicks/drags that follow it
        groups, cur = [], {"key": None, "events": []}
        for ev in part:
            if ev["type"] == "key":
                groups.append(cur)
                cur = {"key": ev["key"], "events": []}
            cur["events"].append(ev)
        groups.append(cur)
        for g in [g for g in groups if g["events"]]:
            key = g["key"]
            acts = [e for e in g["events"] if e["type"] in ("click", "drag")]
            if key is None and acts and acts[0]["type"] == "drag":
                key = drag_card(acts[0], anchor)          # a drag from a card = that card's unit
            if key in tuple("123456") and (acts or zones):
                pref = None
                if acts:
                    a = acts[0]
                    pref = (a["dx2"], a["dy2"]) if a["type"] == "drag" else (a["dx"], a["dy"])
                sleep(0.4)
                place_unit(key, int(key), anchor, pref, zones, roads, view, placed, pview)
            else:
                play_events(g["events"], anchor)         # anything else is replayed exactly as recorded
    pydirectinput.moveTo(anchor[0] - 420, anchor[1] - 250)  # park the cursor off the units


def click_screen_middle():
    """Click the middle of the Roblox window once. Some maps (Wizard World, like Dungeon Train) open a pop-up when the
    match loads, and it eats the first clicks; a click in the middle dismisses it before any unit is placed."""
    rect = roblox_rect()
    if rect:
        l, t, r, b = rect
    else:
        h, w = screenshot().shape[:2]
        l, t, r, b = 0, 0, w, h
    x, y = int((l + r) / 2), int((t + b) / 2)
    log(f"Clicking the middle of the screen ({x}, {y}) to clear any pop-up before placing units.")
    click(x, y)
    sleep(0.6)


def place_units(anchor):
    """Replay the saved placements for this map, or record them if there are none yet."""
    click_screen_middle()
    path = placement_path(current_map) if current_map else None
    if path and path.exists():
        try:
            events = json.loads(path.read_text(encoding="utf-8"))["events"]
            if not isinstance(events, list):
                raise ValueError("'events' isn't a list")
        except (OSError, ValueError, KeyError, TypeError) as e:
            # A damaged file used to crash every run on this map forever. Keep it aside and record afresh.
            bad = path.with_name(path.name + ".bad")
            try:
                os.replace(path, bad)
            except OSError:
                pass
            log(f"{path.name} is damaged ({e}); kept as {bad.name}. Recording this map again.")
        else:
            log(f"Placing units from {path.name} ({len(events)} actions).")
            replay_placements(events, anchor)
            return
    if not path:
        log(f">>> Couldn't tell which map this is. Place your units, then press {record_key_label()}. <<<")
        activity(f"Waiting for you: place units, then press {record_key_label()}")
        go_flag.clear()
        event("busy", on=True)          # a person is needed here, so BMO's watchdog stands down
        try:
            while not go_flag.is_set():
                sleep(0.2)
        finally:
            event("busy", on=False)
        return
    log(f">>> No saved placements for {current_map} yet. RECORDING: place your units "
        f"like normal (don't press Start Wave), then press {record_key_label()}. <<<")
    activity(f"Recording placements - press {record_key_label()} when done")
    event("busy", on=True)
    try:
        events = record_placements(anchor)
    finally:
        event("busy", on=False)
    PLACEMENTS.mkdir(exist_ok=True)
    common.write_json_atomic(path, {"map": current_map, "events": events}, indent=1)
    log(f"Saved {len(events)} actions to {path.name}. It will be replayed from now on.")


# ---------------- car choice ----------------
def choose_car(play_hits, claimed_hits):
    """
    play_hits / claimed_hits: lists of (x, y, score).
    Returns (index, reason) where index is 0-based, left to right.
    A "Bonus Claimed" badge belongs to the car whose Play button is the
    closest one at or to the left of the badge.
    """
    plays = sorted(play_hits, key=lambda h: h[0])
    claimed = [False] * len(plays)
    for bx, _, _ in claimed_hits:
        owners = [i for i, p in enumerate(plays) if p[0] <= bx]
        if owners:
            claimed[owners[-1]] = True
    for i, done in enumerate(claimed):
        if not done:
            return i, "bonus available"
    return min(FALLBACK_CAR - 1, len(plays) - 1), "all bonuses claimed, running for base rewards"


# ---------------- steps ----------------
def go_to_area(name):
    """Open the Areas menu (M) and click Go on the named area's card. Right after joining or
    reconnecting, the hub shows banners/pop-ups (Welcome back, Daily Rewards) and ignores M for a
    few seconds, so try a few times, clearing pop-ups in between."""
    pos = find_card(name)                    # the Areas menu may already be open
    if not pos and not wait_for_hub():       # never press M while the game is still loading
        return False
    for attempt in range(1, 4):
        if pos:
            break
        if dismiss_daily_rewards():
            sleep(1)
        press("m")
        pos = wait_until(lambda: find_card(name), timeout=8)
        if not pos and attempt < 3:
            log(f"The Areas menu didn't open (try {attempt}/3), waiting a bit and pressing M again.")
            sleep(3)
            if not find_card(name) and not wait_for_hub(timeout=60):   # a loading screen ate the key: wait it out
                return False
    if not pos:
        log(f"Couldn't find the {name} card in the Areas menu (best match "
            f"{match_card(name)[0]:.2f}, need 0.80).")
        save_debug(f"areas_{name}")
        return False
    click(*pos)
    return True


def open_challenge_menu():
    if len(find_all("play")) >= 1:
        return True
    if not wait_for("take_challenge", timeout=30):
        log(f"Never saw the Take a Challenge prompt (best match {best_score('take_challenge'):.2f}, need "
            f"{THRESHOLD:.2f}; auto-scale x{ui_scale():.2f}). The debug screenshot shows what I saw.")
        save_debug("take_challenge")
        return False
    press("e")
    return True


def pick_car():
    end = time.time() + MATCH_TIMEOUT
    plays = []
    steady = last_n = 0
    while time.time() < end:
        screen = screenshot()
        plays = find_all("play", screen)
        if len(plays) >= 3:
            break
        # fewer than 3 found: if the same number has shown for 4 looks in a row (about 1.5s), the menu
        # has finished drawing and waiting longer won't turn up more Play buttons
        steady = steady + 1 if (plays and len(plays) == last_n) else 0
        last_n = len(plays)
        if steady >= 4:
            log(f"Only {len(plays)} Play button(s) matched and it's not changing; carrying on with those.")
            break
        sleep(0.4)
    if not plays:
        log(f"No Play buttons found (best match {best_score('play'):.2f}, need {THRESHOLD:.2f}; "
            f"auto-scale x{ui_scale():.2f}). Is the car menu open? The debug screenshot shows what I saw.")
        save_debug("car_menu")
        return False
    sleep(0.5)  # let the badges finish fading in
    screen = screenshot()
    plays = find_all("play", screen)
    if not plays:
        log("The Play buttons disappeared (lag or the menu closed).")
        save_debug("car_menu")
        return False
    claimed = find_all("bonus_claimed", screen, threshold=0.75)
    idx, reason = choose_car(plays, claimed)
    plays.sort(key=lambda h: h[0])
    global current_map
    current_map = detect_map(screen, plays[idx][0])
    log(f"Car {idx + 1} ({current_map or 'unknown'} map): {reason} "
        f"({len(plays)} cars, {len(claimed)} claimed).")
    click(plays[idx][0], plays[idx][1])
    return True


def find_upgrade_all(screen, title):
    """The Upgrade All button is a wide yellow bar below the 'YOUR HEROES' title when
    you can afford it, and grey when you can't. Finding it by color means hover
    effects, the changing price and the text shifting around don't matter."""
    hsv = hsv_of(screen)
    mask = cv2.inRange(hsv, (8, 60, 200), (32, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if title is not None:
            where = y > title[1] + 50 and abs(x + w // 2 - title[0]) < 200
        else:               # title picture not found: it's the wide yellow bar low in the right-hand panel
            where = x + w // 2 > 0.6 * screen.shape[1] and y > 0.35 * screen.shape[0]
        if w >= 170 and 20 <= h <= 70 and w >= 6 * h and where:
            if best is None or y > best[1]:
                best = (x, y, w, h)
    return (best[0] + best[2] // 2, best[1] + best[3] // 2) if best else None


WAVE_SEGMENTS = ((0, 74), (76, 92), (90, 106))  # "WAVE", "3", "2" inside templates/wave32.png


def wave_reached(screen):
    """True only when the HUD reads exactly 'WAVE 32'. Checking each character separately
    stops 'WAVE 2' or 'WAVE 3' from passing as a match."""
    tpl = template("wave32")
    h, w = tpl.shape[:2]
    result = match(screen, tpl)
    _, score, _, (x, y) = cv2.minMaxLoc(result)
    if score < 0.8:
        return False
    region = screen[y:y + h, x:x + w]
    for a, b in WAVE_SEGMENTS:
        seg = float(match(region[:, a:b], tpl[:, a:b])[0, 0])
        if not seg >= 0.9:
            return False
    return True


# ---------------- wave counter reader ----------------
from bmo_wave_glyphs import WAVE_GLYPHS   # noqa: E402  (the HUD letter shapes; data lives in its own file)
_glyph_masks = {}


def _glyph(ch):
    if ch not in _glyph_masks:
        _glyph_masks[ch] = np.array([[c == "#" for c in row] for row in WAVE_GLYPHS[ch]], dtype=np.uint8)
    return _glyph_masks[ch]


def _glyph_score(comp, ch):
    """How well a white blob (0/1 mask) matches one HUD glyph, 0..1."""
    g = _glyph(ch)
    h, w = comp.shape
    gh, gw = g.shape
    if abs(h - gh) > 2 or abs(w - gw) > 2:
        return 0.0
    if comp.shape != g.shape:
        comp = cv2.resize(comp, (gw, gh), interpolation=cv2.INTER_NEAREST)
    inter = float((comp & g).sum())
    union = float((comp | g).sum())
    return inter / union if union else 0.0


_wave_k = [None]            # the zoom at which the wave counter was last read (tried first)


def read_wave(screen):
    """The wave number shown in the HUD ("WAVE 17" -> 17), or None when it can't be read.
    The HUD letters are ~18px tall in a 1280x720 window but only ~15px on the shrunk 1920x1080 screen, so the
    HUD strip is also tried enlarged."""
    rect = roblox_rect()
    if rect:
        l, t, r, b = rect
        left, top = max(0, int(l)), max(0, int(t))
        view = screen[top:top + int((b - t) * 0.35), left:int(r)]
    else:
        view = screen[:int(screen.shape[0] * 0.35)]
    if view.size == 0:
        return None
    tries = [_wave_k[0]] if _wave_k[0] else []
    tries += [k for k in (1.0, 1.27, 1.5, 1.15) if k not in tries]
    for k in tries:
        got = _read_wave_at(view if k == 1.0 else cv2.resize(view, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC))
        if got is not None:
            _wave_k[0] = k
            return got
    return None


def _read_wave_at(view):
    mask = (view.min(axis=2) > 225).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 15 <= h <= 21 and 7 <= w <= 25 and area >= 80:
            blobs.append((int(x), int(y), int(w), int(h), i))
    blobs.sort()
    for start in blobs:
        if _glyph_score((lab[start[1]:start[1] + start[3], start[0]:start[0] + start[2]] == start[4]).astype(np.uint8), "W") < 0.7:
            continue
        chain, prev = [start], start
        while True:
            nxt = [b for b in blobs if b[0] > prev[0] and -3 <= b[0] - (prev[0] + prev[2]) <= 9 and abs(b[1] - start[1]) <= 3]
            if not nxt:
                break
            prev = nxt[0]
            chain.append(prev)
        if len(chain) < 5:
            continue
        chars = []
        for b in chain:
            comp = (lab[b[1]:b[1] + b[3], b[0]:b[0] + b[2]] == b[4]).astype(np.uint8)
            best = max((( _glyph_score(comp, ch), ch) for ch in "WAVE0123456789"), key=lambda s: s[0])
            chars.append(best[1] if best[0] >= 0.6 else "?")
        if "".join(chars[:4]) != "WAVE":
            continue
        digits = "".join(chars[4:])
        if digits.isdigit() and len(digits) <= 3:
            return int(digits)
    return None



POPUP_CLICKS = [(0, 130), (0, 60), (0, 190)]  # offsets from the "CHALLENGE!" banner into the popup body


def dismiss_popup():
    """Click the middle of the challenge popup until it's gone."""
    for attempt in range(4):
        popup = find("challenge_popup")
        if not popup:
            return True
        dx, dy = POPUP_CLICKS[attempt % len(POPUP_CLICKS)]
        log("Dismissing the challenge popup.")
        click(popup[0] + dx, popup[1] + dy)
        sleep(0.8)
    return find("challenge_popup") is None


def click_start_wave():
    """Click the middle of the Start Wave button and make sure the wave really started."""
    for attempt in range(1, 5):
        screen = screenshot()
        pos = find("start_match", screen, 0.85) or find_wave_button(screen) or find("start_match", screen, 0.75)
        if not pos:
            return True  # no button left to press
        click(*pos, hover=0.25)
        sleep(1.2)
        if not find("start_match", threshold=0.75):  # the "START WAVE 1" text is gone = started
            return True
        log(f"Start Wave didn't register, clicking it again ({attempt}/4).")
    return False


def stable_anchor(first):
    """Unit placements are measured from the Start Wave button, so a button position that is a few pixels off
    moves every unit. Look several times and take the middle value; prefer the picture match over the pink-bar
    finder because the picture is exact."""
    tpl_hits, bar_hits = [], []
    if first:
        bar_hits.append(tuple(first))
    for _ in range(5):
        screen = screenshot()
        t = find("start_match", screen, 0.75)
        if t:
            tpl_hits.append(tuple(t))
        else:
            b = find_wave_button(screen)
            if b:
                bar_hits.append(tuple(b))
        sleep(0.12)
    pts = tpl_hits if len(tpl_hits) >= 2 else (tpl_hits + bar_hits)
    if not pts:
        return first
    xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    anchor = (int(xs[len(xs) // 2]), int(ys[len(ys) // 2]))
    spread = (max(xs) - min(xs), max(ys) - min(ys))
    log(f"Start Wave anchor ({anchor[0]}, {anchor[1]}) from {len(pts)} looks "
        f"({'picture' if pts is tpl_hits else 'pink bar'}, spread {spread[0]}x{spread[1]}px).")
    return anchor


def start_match():
    activity("Waiting for the match to load")
    run_info["t0"], run_info["wave"] = time.time(), 0
    # wait until the match has loaded (a Start Wave button is on screen)
    def start_button():
        screen = screenshot()
        return find("start_match", screen, 0.75) or find_wave_button(screen)
    pos = wait_until(start_button, timeout=45)
    if not pos:
        log("Didn't see the Start Wave button (maybe it already started).")
        save_debug("start_wave")
        return
    sleep(1.0)

    if not dismiss_popup():
        log("The challenge popup won't go away; continuing anyway.")

    anchor = stable_anchor(pos)
    activity("Placing units")
    place_units(anchor)

    sleep(0.7)  # let the last placement finish before pressing Start Wave
    activity("Starting wave 1")
    log("Starting the match.")
    if not click_start_wave():
        log("Start Wave still isn't pressed; the match loop will keep trying.")
        save_debug("start_wave_click")


def find_lobby_button():
    """The Lobby button has two looks (lobby.png: yellow with the LOBBY label, lobby_2.png: green).
    Check both on the same frame and return whichever is there."""
    for name in ("lobby", "lobby_2"):
        if template(name, required=False) is None:
            continue
        pos = find(name)
        if pos:
            return pos
    return None


def lobby_fallback_pos(screen):
    """Where the Lobby (house) button sits when its picture can't be matched: about 86.6% across and
    8.6% down the Roblox window (measured on a 1280x720 frame)."""
    rect = roblox_rect()
    if rect:
        l, t, r, b = rect
    else:
        h, w = screen.shape[:2]
        l, t, r, b = 0, 0, w, h
    return int(l + (r - l) * 0.866), int(t + (b - t) * 0.086)


def go_home_from_match():
    """Lobby button (top right) -> 'Go home?' dialog -> Go Home."""
    activity("Returning to the hub")
    for attempt in range(3):
        lobby = wait_until(find_lobby_button, timeout=8)    # looks for BOTH looks of the button every tick
        if not lobby:
            lobby = lobby_fallback_pos(screenshot())
            log(f"Lobby button picture not matched (best {best_score('lobby'):.2f} / {best_score('lobby_2'):.2f}); "
                f"clicking its usual spot {lobby} instead.")
        click(*lobby)
        gh = wait_for("go_home", timeout=8)
        if gh:
            click(*gh)
            sleep(6)  # loading back to the hub
            return True
        log(f"No 'Go home' dialog after clicking Lobby (try {attempt + 1}/3, "
            f"go_home best {best_score('go_home'):.2f}).")
        save_debug(f"after_lobby_click_{attempt + 1}")
    save_debug("go_home")
    return False


def look_for_play_again(seconds=5.0):
    """Keep looking for the Play Again button for a few seconds: every picture of it (act_play_again, play_again_2 ...),
    first at the normal strictness, then a looser one. Returns its position or None."""
    end = time.time() + seconds
    while time.time() < end:
        control_checkpoint()
        screen = screenshot()
        pos = find("act_play_again", screen) or find("act_play_again", screen, 0.7)
        if pos:
            return pos
        clear_popups(1)
        sleep(0.4)
    return None


replay_pending = [0.0]      # when Play Again was last pressed (the next act run is already loading)


def play_until_end(mode="challenge"):
    """Run the match: Upgrade All, Start/Skip Wave, until the mode's end condition.
    Returns True when the run is finished, or for the raid "victory" (Open Lich is showing) / "none_left" (no portals left),
    or "ended"/False."""
    activity("Match running (upgrading, skipping waves)")
    log("Match running...")
    end = time.time() + MATCH_TIMEOUTS[mode]
    last_n = last_upgrade = last_nudge = last_wave = last_wish = 0.0
    shown_wave, target_hits = None, 0
    event("wave", wave=0)
    freeze_reset()
    while time.time() < end:
        control_checkpoint()
        screen = screenshot()
        check_disconnected(screen)
        freeze_check(screen)
        # Act finished? Check this first, on every tick, before the slower pop-up / wave-counter work below.
        # Numbered acts offer Play Again. Replay directly unless a run/plan stop
        # has already been reached; in that case use Return to Hub instead.
        if mode == "act":
            replay = find("act_play_again", screen)
            hub = find("return_hub", screen)
            plan_finishing = False
            if PLAN and plan_pos[0] < len(PLAN):
                plan_mode, plan_count = PLAN[plan_pos[0]]
                plan_finishing = (plan_mode == "world" and plan_count > 0 and plan_pos[1] + 1 >= plan_count)
            stop_after_this = finish_flag.is_set() or (STOP_RUNS > 0 and completed_runs + 1 >= STOP_RUNS) or plan_finishing
            if replay and not stop_after_this:
                activity("Act complete - playing again")
                event("wave", wave=0)
                log("Act complete. Selecting Play Again.")
                replay_pending[0] = time.time()     # the next run starts on the Start Wave screen, not in the hub
                click(*replay)
                sleep(1.5)      # start_match() below waits for the Start Wave button anyway
                return True
            if hub and not replay and not stop_after_this:
                # Return to Hub is up but Play Again isn't matched: it may still be fading in, or drawn bigger than
                # the picture (a last act has no Next button). Look harder for a few seconds before giving up on it.
                replay = look_for_play_again()
                if replay:
                    activity("Act complete - playing again")
                    event("wave", wave=0)
                    log("Act complete. Selecting Play Again.")
                    replay_pending[0] = time.time()
                    click(*replay)
                    sleep(1.5)
                    return True
                log("Return to Hub is showing but Play Again never matched; going home instead "
                    "(save a picture of Play Again as templates/play_again_2.png to fix this).")
                save_debug("play_again_missing")
            if hub:
                activity("Act complete - returning to the hub")
                event("wave", wave=0)
                log("Act complete. Returning to the hub.")
                click(*hub)
                sleep(4)
                return True

        if handle_interrupts(screen):
            continue

        # ---- Wish Run: a wish popup (three cards) every 5 waves; it blocks everything else until one is chosen ----
        if mode == "wish" and time.time() - last_wish > 1.2:
            last_wish = time.time()
            if pick_wish(screen):
                activity("Picked a wish")
                sleep(1.5)
                last_wish = time.time()
                continue

        # ---- wave counter (shown in BMO) ----
        wv = read_wave(screen)
        if wv is not None and wv != shown_wave:
            shown_wave = wv
            run_info["wave"] = max(run_info["wave"], wv)
            event("wave", wave=wv)
        target_hits = target_hits + 1 if (TARGET_WAVE > 0 and wv is not None and wv >= TARGET_WAVE) else 0

        # ---- end conditions ----
        if is_endless(mode) and TARGET_WAVE > 0 and ((TARGET_WAVE == 32 and wave_reached(screen)) or target_hits >= 2):
            log(f"Reached wave {TARGET_WAVE}.")
            save_debug("wave_reached")  # so a wrong detection can be checked afterwards
            return go_home_from_match()
        if mode == "raid":
            if find("none_left", screen):
                log("Raid over - no Lich Portals left.")
                return "none_left"
            if find("next_btn", screen) or find("open_lich", screen):     # Next comes first; Open Lich only after Next
                log("Raid won.")
                return "victory"

        hub = find("return_hub", screen)
        if hub and mode == "raid":
            # The raid end screen can show Return to Hub before the Next button has faded in (or
            # while Next doesn't match yet). Going home straight away skipped the next portal, so wait.
            log("Raid end screen showing - waiting for the Next button before going home...")
            wait_end = time.time() + 12
            while time.time() < wait_end:
                control_checkpoint()
                if clear_popups(1):                 # a unit-drop pop-up (AWESOME!) can sit on top of the Next button
                    wait_end += 3
                s2 = screenshot()
                if find("none_left", s2):
                    log("Raid over - no Lich Portals left.")
                    return "none_left"
                if find("next_btn", s2) or find("next_btn", s2, 0.7) or find("open_lich", s2):
                    log("Raid won.")
                    return "victory"
                sleep(0.5)
            log("The Next button never showed on the end screen; going home.")
            save_debug("raid_end_no_next")
        if hub:
            activity("Match over - returning to the hub")
            event("wave", wave=0)
            log("Match over.")
            sleep(1.0)
            click(*hub)
            sleep(4)  # loading back to the hub
            return "ended" if mode != "challenge" else True

        # ---- Units panel + Upgrade All (hotkey N is a toggle: only press when it's closed) ----
        title = find("heroes_title", screen)
        bar = None
        if not title:
            bar = find_upgrade_all(screen, None)  # the title picture missed, but the Upgrade All bar is showing
            title = bar
        if not title:
            if time.time() - last_n > 4:
                press("n")
                last_n = time.time()
        elif time.time() - last_upgrade > UPGRADE_EVERY:
            up = bar or find_upgrade_all(screen, title)  # only exists while you can afford it
            if up:
                click(*up)
                # park the cursor away so the next click is a fresh hover
                px, py = max(0, up[0] - 420), max(0, up[1] - 150)
                pydirectinput.moveTo(px, py)
                mouse_pos[:] = [px, py]
            last_upgrade = time.time()

        # ---- Start Wave / Skip Wave whenever it's lit up ----
        if time.time() - last_wave > WAVE_EVERY:
            wave = find_wave_button(screen)
            if wave:
                click(*wave, hover=0.15)
            last_wave = time.time()

        if time.time() - last_nudge > 45:   # keep Roblox from kicking us for being idle
            nudge()
            last_nudge = time.time()
        sleep(0.5)
    log("The match never reached its end (timed out).")
    save_debug("end_screen")
    return False


# ---------------- Wish Run ----------------
# Every 5 waves a popup shows three wish cards, each with a WISH IT! button. The cards are coloured by rarity:
# blue = Common, purple = Rare, gold = Legendary. templates/wish_it.png is only the button's lettering, so it
# matches all three buttons (grayscale); the rarity is read from the colour of the button itself.
WISH_RANK = {"common": 0, "rare": 1, "legendary": 2}
WISH_HUES = {"common": (90, 116), "rare": (120, 152), "legendary": (8, 35)}   # OpenCV hue (0-179)
WISH_MATCH = 0.62            # how well the WISH IT! lettering must match (grayscale; the button colour differs per card)


def wish_rarity_at(hsv, cx, cy, tw, th):
    """Rarity of the card whose WISH IT! lettering is centred at (cx, cy): the median hue of the coloured button
    around the text (the white lettering and the dark outline are left out)."""
    H, W = hsv.shape[:2]
    x0, x1 = max(0, int(cx - tw * 1.3)), min(W, int(cx + tw * 1.3))
    y0, y1 = max(0, int(cy - th * 0.8)), min(H, int(cy + th * 0.8))
    reg = hsv[y0:y1, x0:x1].reshape(-1, 3)
    reg = reg[(reg[:, 1] > 70) & (reg[:, 2] > 120)]
    if len(reg) < 40:
        return None
    hue = float(np.median(reg[:, 0]))
    for name, (lo, hi) in WISH_HUES.items():
        if lo <= hue <= hi:
            return name
    return None


def _wish_by_text(screen):
    """[(rarity, x, y)] for every WISH IT! button found by its lettering."""
    if template("wish_it", required=False) is None:
        return []
    gray = gray_of(screen)
    hsv = hsv_of(screen)
    base = cv2.cvtColor(template("wish_it"), cv2.COLOR_BGR2GRAY)
    best = ([], -1.0)
    for sc in _scale_list(5):
        t = cv2.resize(base, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
        th, tw = t.shape[:2]
        if th >= gray.shape[0] or tw >= gray.shape[1]:
            continue
        res = match(gray, t)
        ys, xs = np.where(res >= WISH_MATCH)
        kept = []
        for x, y in sorted(zip(xs, ys), key=lambda p: -res[p[1], p[0]]):
            if all(abs(x - kx) > tw // 2 or abs(y - ky) > th // 2 for kx, ky, _ in kept):
                kept.append((int(x), int(y), float(res[y, x])))
            if len(kept) == 3:
                break
        score = len(kept) + (sum(k[2] for k in kept) / len(kept) if kept else 0.0)
        if score > best[1]:
            best = ([(x + tw // 2, y + th // 2, tw, th) for x, y, _ in kept], score)
        if len(kept) == 3 and all(k[2] >= 0.8 for k in kept):
            break
    return [(wish_rarity_at(hsv, cx, cy, tw, th), cx, cy) for cx, cy, tw, th in best[0]]


def _wish_by_colour(screen):
    """Fallback when the lettering isn't matched: find the three tall coloured cards and click near the bottom of
    each, where the WISH IT! button sits. Returns [(rarity, x, y)] left to right."""
    crop, (ox, oy) = roblox_crop(screen)
    H, W = crop.shape[:2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    out = []
    for name, (lo, hi) in WISH_HUES.items():
        mask = cv2.inRange(hsv, (lo, 90, 150), (hi, 255, 255))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if 0.15 * W <= w <= 0.36 * W and 0.45 * H <= h <= H and cv2.contourArea(c) >= 0.5 * w * h:
                out.append((name, ox + x + w // 2, oy + y + h - int(0.082 * H)))
    return sorted(out, key=lambda o: o[1])[:3]


def find_wish_choices(screen):
    """[(rarity, x, y)] for the wish cards on screen (empty when there is no wish popup)."""
    found = _wish_by_text(screen)
    if len(found) < 3:                         # lettering matched fewer than the 3 cards: try the card colours too
        by_colour = _wish_by_colour(screen)
        if len(by_colour) > len(found):
            found = by_colour
    return found


def pick_wish(screen):
    """If the wish popup is up, click the best card: Legendary over Rare over Common; a tie is picked at random.
    Returns True when a card was clicked."""
    with no_sweep():
        choices = find_wish_choices(screen)
    if not choices:
        return False
    best = max(WISH_RANK.get(r, -1) for r, _, _ in choices)
    top = [c for c in choices if WISH_RANK.get(c[0], -1) == best]
    rarity, x, y = random.choice(top)
    log("Wish popup: " + ", ".join(r or "?" for r, _, _ in choices) + f" -> wishing for the {rarity or 'unknown'} one.")
    click(x, y, hover=0.2)
    return True


# ---------------- modes ----------------
def run_challenge():
    """One Dungeon Train challenge."""
    if not prepare():
        return False
    activity("Heading to Dungeon Train")
    return (
        go_to_area("dungeon")
        and open_challenge_menu()
        and pick_car()
        and (start_match() or True)
        and play_until_end("challenge")
    )


completed_runs = 0

def request_finish(reason):
    """Stop cleanly once the current run is done (also what BMO's FINISH command does)."""
    if not finish_flag.is_set():
        finish_flag.set()
        log(f"Finishing: {reason}.")
        event("finish", reason=reason)


def plan_event():
    step, done = plan_pos
    event("plan", step=step, total=len(PLAN), mode=PLAN[step][0], done=done, count=PLAN[step][1])


def plan_advance(why):
    """Move to the next step of the chained plan; its mode is queued like a mode switch from BMO."""
    plan_pos[0] += 1
    plan_pos[1] = 0
    if plan_pos[0] >= len(PLAN):
        if PLAN_LOOP:
            plan_pos[0] = 0
        else:
            plan_pos[0] = len(PLAN) - 1
            request_finish("the plan is complete")
            event("plan_done")
            return
    pending_mode[0] = PLAN[plan_pos[0]][0]
    log(f"Plan ({why}): next up is {pending_mode[0]}.")
    plan_event()


def plan_progress(kind):
    if not PLAN:
        return
    mode, count = PLAN[plan_pos[0]]
    if kind != mode:
        return              # e.g. the raid fell back to challenges because the portals ran out
    plan_pos[1] += 1
    plan_event()
    if count and plan_pos[1] >= count:
        plan_advance(f"{count} {mode} done")


def record_completed_run(kind):
    global completed_runs
    completed_runs += 1
    log(f"Completed runs: {completed_runs}")
    t0 = run_info["t0"]
    event("run", mode=kind, dur=round(time.time() - t0, 1) if t0 else None,
          wave=run_info["wave"] or None, map=current_map)
    run_info["t0"], run_info["wave"] = None, 0
    plan_progress(kind)
    if STOP_RUNS and completed_runs >= STOP_RUNS:
        request_finish(f"reached {STOP_RUNS} runs")


# ---------------- Lich Portal raid ----------------
# Flow: Items (hotkey B, or the items.png button) -> "Your Items" popup (your_items.png) -> lich_portal_icon
# (scroll the popup if it isn't showing) -> open_lich_portal -> lich_enter -> battle on the Lich map.
# After the battle: next_btn -> open_lich -> yes, re-place the units and play again. When the battle ends with
# none_left showing there are no portals left: back -> back home, then carry on with the plan (or challenges).
LICH_SCROLL_AT = (0, 220)     # where to park the cursor inside the Your Items popup, relative to its title, so the wheel scrolls it
LICH_SCROLL_TRIES = 8         # wheel attempts downwards (the portal is usually at the bottom) before trying upwards
ITEMS_HOTKEY = "b"


def find_home_button(screen=None):
    """The three ways home: the end screen's Return to Hub (return_hub.png) or the Lobby button, which has two
    looks (lobby.png / lobby_2.png). Returns ('hub' or 'lobby', position), or (None, None)."""
    screen = screen if screen is not None else screenshot()
    pos = find("return_hub", screen)
    if pos:
        return "hub", pos
    for name in ("lobby", "lobby_2"):
        if template(name, required=False) is None:
            continue
        pos = find(name, screen)
        if pos:
            return "lobby", pos
    return None, None


def park_mouse():
    """Move the cursor away from where we last clicked. A button the mouse is resting on is drawn bigger (hover), which
    stopped Return to Hub from matching after the last Lich Portal."""
    x, y = mouse_pos
    px, py = max(5, x - 420), max(5, y - 150)
    pydirectinput.moveTo(px, py)
    mouse_pos[:] = [px, py]
    sleep(0.4)


def find_green_bar(screen, x_frac=(0.2, 0.65), y_frac=(0.5, 0.92)):
    """Last resort for Return to Hub: the wide green rounded bar in the lower middle of the end panel, found by colour and
    shape. Returns its centre or None."""
    H, W = screen.shape[:2]
    mask = cv2.inRange(hsv_of(screen), (38, 90, 140), (88, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 9)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        fill = cv2.contourArea(c) / float(w * h or 1)
        cx, cy = x + w // 2, y + h // 2
        if (200 <= w <= 420 and 45 <= h <= 110 and 2.4 <= w / h <= 6.5 and fill >= 0.55
                and x_frac[0] * W <= cx <= x_frac[1] * W and y_frac[0] * H <= cy <= y_frac[1] * H):
            if best is None or w * h > best[0]:
                best = (w * h, cx, cy)
    return (best[1], best[2]) if best else None


def go_home_any():
    """Get back to the hub using whichever of return_hub / lobby / lobby_2 is on screen.
    Return to Hub is one click; the Lobby button also needs the 'Go home?' dialog (go_home_from_match)."""
    activity("Returning to the hub")
    clear_popups()
    park_mouse()
    found = wait_until(lambda: (lambda k, p: (k, p) if k else None)(*find_home_button()), timeout=12)
    if not found:
        # nothing matched (hover/size differences): look once more, loosely, then by colour
        screen = screenshot()
        pos = find("return_hub", screen, 0.7)
        if pos:
            found = ("hub", pos)
        else:
            bar = find_green_bar(screen)
            if bar and not find_lobby_button():
                log(f"Return to Hub picture not matched (best {best_score('return_hub'):.2f}); "
                    f"clicking the green button at {bar}.")
                found = ("hub", bar)
    if found and found[0] == "hub":
        click(*found[1])
        sleep(4)                    # loading back to the hub
        return True
    if found:
        return go_home_from_match()
    log("Couldn't find Return to Hub or the Lobby button. Trying the Lobby button's usual spot.")
    save_debug("go_home_any")
    return go_home_from_match()


def open_items_popup():
    """Open the Items menu (hotkey B; the items button is the fallback) and wait for the Your Items popup."""
    for attempt in range(3):
        if find("your_items"):                 # B is a toggle, so look before pressing it
            return True
        activity("Opening Items")
        press(ITEMS_HOTKEY)
        if wait_for("your_items", timeout=4):
            return True
        btn = find("items")
        if btn:
            log("The B hotkey didn't open Items; clicking the items button.")
            click(*btn)
            if wait_for("your_items", timeout=4):
                return True
    log("The Your Items popup never opened.")
    save_debug("your_items")
    return False


def find_lich_portal():
    """Find lich_portal_icon in the Your Items popup, scrolling the list when it isn't showing."""
    pos = find("lich_portal_icon")
    if pos:
        return pos
    title = find("your_items")
    for i in range(LICH_SCROLL_TRIES + 4):
        if title:                              # the wheel only works with the cursor over the list
            hx, hy = title[0] + LICH_SCROLL_AT[0], title[1] + LICH_SCROLL_AT[1]
            pydirectinput.moveTo(hx - 20, hy)
            sleep(0.1)
            pydirectinput.moveTo(hx, hy)
            sleep(0.2)
        scroll_down(3 if i < LICH_SCROLL_TRIES else -3)    # down first (it is usually at the bottom), then back up
        sleep(0.6)
        pos = find("lich_portal_icon")
        if pos:
            return pos
    return None


def lich_open_portal():
    """Items -> Your Items -> Lich Portal -> Open Lich Portal -> Enter. True once we are being teleported."""
    if not open_items_popup():
        return False
    activity("Looking for the Lich Portal")
    icon = find_lich_portal()
    if not icon:
        log("No Lich Portal in your items (lich_portal_icon not found).")
        save_debug("lich_portal_icon")
        return None                            # nothing to open: same as being out of portals
    click(*icon)
    for name, what in (("open_lich_portal", "Open Lich Portal"), ("lich_enter", "Enter")):
        activity(f"Pressing {what}")
        pos = wait_for(name, timeout=15)
        if not pos:
            log(f"Couldn't find the {what} button ({name}).")
            save_debug(name)
            return False
        click(*pos)
    sleep(3)                                   # teleporting to the Lich map
    return True


def run_raid():
    """Lich Portal raid, replayed with portals until you run out."""
    global current_map
    if not prepare():
        return False
    current_map = "lich"
    opened = lich_open_portal()
    if opened is None:
        return lich_out(already_home=True)
    if not opened:
        return False
    return lich_battles()


def lich_battles(first_start=True):
    """Fight, then Open Lich -> Next -> Yes, re-place the units, and go again. Returns what the main loop should do."""
    place = first_start
    while True:
        if place:
            start_match()
        place = True
        result = play_until_end("raid")
        if result not in ("victory", "none_left"):
            return bool(result)
        record_completed_run("raid")
        if result == "none_left":
            return lich_out()
        want = raid_wants_out()
        if want:
            lich_step_out(want)
            return want
        again = lich_play_again()
        if again == "none_left":
            return lich_out()
        if not again:
            return False


def lich_out(already_home=False):
    """No Lich Portals left. Back -> home, then the plan carries on (or challenges). Returns what the main loop should do."""
    event("milk_out")                          # the event name is kept so BMO's stop-on-empty setting still works
    activity("Out of Lich Portals")
    log("Out of Lich Portals.")
    if not already_home:
        lich_leave()
    if STOP_ON_MILK:
        log("Stopping as requested (out of Lich Portals).")
        stop_reason("milk", "Out of Lich Portals.")
        return "stop"
    if PLAN and PLAN[plan_pos[0]][0] == "raid":
        plan_advance("out of Lich Portals")    # the plan moves on instead of waiting for raids that can't run
    log("Carrying on with the plan." if pending_mode[0] else "Nothing else planned. Switching to the challenge farm.")
    return "challenges"


def raid_wants_out():
    """After a raid win: did BMO ask us to finish ("stop") or to switch mode ("switch")?"""
    if finish_flag.is_set():
        return "stop"
    if pending_mode[0] and pending_mode[0] != "raid":
        return "switch"
    return None


def lich_leave():
    """Leave the Lich map: Back (if it is showing), then home by Return to Hub / Lobby."""
    clear_popups()                              # the AWESOME! unit pop-up can cover Back / Return to Hub
    screen = screenshot()
    back = find("back", screen)
    if not back and not find("return_hub", screen):
        back = wait_for("back", timeout=6)      # neither is showing yet: give the end screen a moment
    if back:
        click(*back)
        sleep(1)
    else:
        log("No Back button on the end screen; going home directly.")
    if not go_home_any():
        save_debug("lich_end")


def lich_step_out(why):
    """BMO asked us to finish or switch modes, so don't open another portal: just go home."""
    log("Leaving the raid: " + ("finishing as requested." if why == "stop" else "switching mode."))
    activity("Leaving the raid")
    lich_leave()


def lich_play_again():
    """End screen: Next -> Open Lich -> Yes. Returns True when the next battle is loading, "none_left" when the
    Next screen has no portal to open, or False if a button never showed up."""
    activity("Raid won - opening the next portal")
    clear_popups()                             # a unit-drop pop-up (AWESOME!) after the raid covers the buttons
    # 1. Next (skipped if Open Lich is already showing, e.g. Next was pressed by hand)
    nxt = wait_until(lambda: find("next_btn"), timeout=10)
    if nxt:
        click(*nxt)
        sleep(1)
    elif not find("open_lich"):
        if find("none_left"):
            return "none_left"
        log("Couldn't find the Next button (next_btn).")
        save_debug("next_btn")
        return False

    # 2. Open Lich, or the none-left screen when there is no portal to open
    def open_or_none():
        screen = screenshot()
        if find("none_left", screen):
            return "none_left"
        return find("open_lich", screen) or None
    got = wait_until(open_or_none, timeout=10)
    if got == "none_left":
        log("No Lich Portals left after Next.")
        return "none_left"
    if not got:
        log("Couldn't find the Open Lich button (open_lich).")
        save_debug("open_lich")
        return False
    click(*got)
    sleep(1)

    # 3. Yes
    yes = wait_for("yes", timeout=10)
    if not yes:
        log("Couldn't find the Yes button (yes).")
        save_debug("yes")
        return False
    click(*yes)
    log("Playing the raid again.")
    sleep(3)
    return True


PORTAL_TEMPLATES = ("play_ui", "nightosphere", "infinite", "portal_play", "portal_start")
LIST_OFFSET = (-56, 190)       # world list, relative to the Play UI header (templates/play_ui.png)
INFINITE_OFFSET = (0, -116)    # Infinite Mode card, relative to its "Bug Milk drops here" banner


def scroll_down(clicks=3):
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, -120 * clicks, 0)  # mouse wheel, Windows only


WORLD_NAMES = {   # key -> (label, picture(s) of its card in the world list, most specific first)
    "candy": ("Candy Kingdom", ("candy_world",)),
    "ice": ("Ice Kingdom", ("ice_world",)),
    "fire": ("Fire Kingdom", ("fire_world",)),
    "lemon": ("Castle Lemongrab", ("lemon_world",)),
    "crystal": ("Crystal Dimension", ("crystal_world",)),
    "factory": ("Mo Factory", ("factory_world",)),
    "nightosphere": ("Nightosphere", ("nightosphere_world", "nightosphere")),
    "lumpy": ("Lumpy World", ("lumpy_world",)),            # Update 2
    "wizard": ("Wizard World", ("wizard_world",)),         # Update 2
}
WORLD_ALIASES = {"candy kingdom": "candy", "ice kingdom": "ice", "fire kingdom": "fire", "lemongrab": "lemon",
                 "castle lemongrab": "lemon", "crystal dimension": "crystal", "mo factory": "factory",
                 "mo": "factory", "night": "nightosphere",
                 "lumpy world": "lumpy", "lumpy space": "lumpy", "wizard world": "wizard"}


def norm_world(text):
    t = str(text or "").strip().lower().replace("_", " ")
    if t.endswith(" world"):
        t = t[:-6].strip()
    return t if t in WORLD_NAMES else WORLD_ALIASES.get(t, t)


def norm_act(text):
    t = str(text or "").strip().lower().replace("_", " ")
    if t.replace(" ", "") in ("wish", "wishrun"):
        return "wish"
    t = t.replace("act", "").strip()
    return "infinite" if t in ("", "inf", "infinite", "endless") else t


def is_endless(act):
    """Infinite and Wish Run are both endless: no Play Again, the match ends at the target wave or on defeat."""
    return act in ("infinite", "wish")


def stage_names(world, act):
    """Picture names for the stage card, most likely first: act_endless, act_wish, or act_<world>_<n>.
    (act_<world>_world_<n> is accepted too, in case a picture was saved with the whole folder name.)"""
    if act == "wish":
        return ["act_wish"]
    if act == "infinite":
        return ["act_endless"]
    return [f"act_{world}_{act}", f"act_{world}_world_{act}"]


def stage_template_name(world, act):
    names = stage_names(world, act)
    return next((n for n in names if template(n, required=False) is not None), names[0])


def portal_templates(world, act):
    """Pictures this world/act needs (the world card, and the stage-name picture for a numbered act)."""
    names = ["card_portal", "play_ui", "portal_play", "portal_start", "arrow_right"]
    names.append(WORLD_NAMES[world][1][0])
    names.append(stage_template_name(world, act))
    if act == "wish":
        names.append("wish_it")        # the WISH IT! button text, needed to pick the wishes
    return names


def pick_world(ui, world):
    """Click the world's card in the list on the left, scrolling down and then up until it shows."""
    names = [n for n in WORLD_NAMES[world][1] if template(n, required=False) is not None]
    def look():
        for n in names:
            pos = find(n)
            if pos:
                return pos
        return None
    pos = look()
    for i in range(12):
        if pos:
            break
        pydirectinput.moveTo(ui[0] + LIST_OFFSET[0] - 20, ui[1] + LIST_OFFSET[1])
        sleep(0.1)
        pydirectinput.moveTo(ui[0] + LIST_OFFSET[0], ui[1] + LIST_OFFSET[1])
        sleep(0.2)
        scroll_down(3 if i < 6 else -3)       # down first, then back up in case it was scrolled past
        sleep(0.6)
        pos = look()
    if not pos:
        return False
    click(*pos)
    sleep(1)
    return True


STAGE_SCALES = (1.0, 1.08, 1.16, 1.24, 1.32, 1.42, 0.94, 0.88, 0.8)   # the selected card is drawn ~15-30% bigger
STAGE_ACCEPT = 0.78


def find_stage_card(target, screen=None):
    """The act's card, at its normal size or at the bigger size it has once it is selected (the stage-name picture is
    searched at several sizes). Returns (position, is_enlarged_guess) or (None, False)."""
    screen = screen if screen is not None else screenshot()
    pos = find(target, screen)
    if pos:
        return pos, False
    for n in variant_names(target):
        if template(n, required=False) is None:
            continue
        score, where = find_wide(n, screen, STAGE_ACCEPT, STAGE_SCALES)
        if where is not None and score >= STAGE_ACCEPT:
            return where, True
    return None, False


def pick_stage(world, act):
    """Click the numbered act (or Infinite Mode) card once. There is no 'is it selected' check any more: it kept
    failing and made the macro click the card again and again. The real proof that the act is selected is the
    Play button showing up, and run_portal() looks for that (clicking the card again only if it doesn't)."""
    target = stage_template_name(world, act)
    label = {"infinite": "Infinite Mode", "wish": "Wish Run"}.get(act, f"Act {act}")
    sleep(0.8)                                   # the stage cards slide in after a world is picked

    pos, big = find_stage_card(target)
    # page through the cards with the arrows (only the arrow pictures that exist)
    order = [n for n in ["arrow_right", "arrow_left", "arrow_left", "arrow_right"] * 2
             if template(n, required=False) is not None]
    for name in order:
        if pos:
            break
        arrow = find(name)
        if arrow:
            log(f"{label} isn't on this page - clicking {name}.")
            click(*arrow)
            sleep(0.9)
        pos, big = find_stage_card(target)
    if not pos:
        log(f"Couldn't see the {label} card for this world (best match {best_score(target):.2f}, need {THRESHOLD:.2f}).")
        return False
    if big:
        log(f"{label} is drawn bigger than usual (it is probably already selected). Clicking it at ({pos[0]}, {pos[1]}).")
    else:
        log(f"Clicking {label} at ({pos[0]}, {pos[1]}).")
    click(*pos)
    sleep(0.9)
    return True


def walk_until(key, name, timeout=25):
    """Hold `key` until the picture `name` shows up, then let go at once.
    Tight loop on purpose: the generic wait_for() also runs pop-up/disconnect checks and an expensive size
    search on every tick, which at 1920x1080 made the character keep walking well past the Play screen.
    Here each tick is one grab + one cheap match; the size search runs only every few seconds."""
    started = last_sweep = time.time()
    pos = None
    pydirectinput.keyDown(key)
    try:
        while time.time() - started < timeout:
            control_checkpoint()
            frame = screenshot()
            with pinned_frame(frame):
                pos = find(name)
                if pos is None and time.time() - started > 2 and time.time() - last_sweep > 3:
                    last_sweep = time.time()
                    score, _, where = sweep_find(name, frame)
                    if where is not None and score >= max(SWEEP_ACCEPT, THRESHOLD):
                        pos = where
                if pos is None:
                    check_disconnected()
            if pos:
                break
            time.sleep(0.03)
    finally:
        pydirectinput.keyUp(key)
    return pos


try:
    PORTAL_WALK_SECS = max(0.5, min(5.0, float(os.getenv("CANDY_PORTAL_WALK", "2.0"))))   # most we ever walk forward in one go (BMO > Settings > Macro slider)
except ValueError:
    PORTAL_WALK_SECS = 2.0


def hold_key(key, secs):
    pydirectinput.keyDown(key)
    try:
        t_end = time.time() + secs
        while time.time() < t_end:
            control_checkpoint()
            time.sleep(0.05)
    finally:
        pydirectinput.keyUp(key)


def reach_play_ui():
    """Walk into the portal until the Play screen shows, but never for long.
    While the game lags the Play screen can show up late, and walking until it appeared made the character walk
    straight past the portal. Now: walk at most PORTAL_WALK_SECS (stopping the moment the screen shows), stand
    still and give the screen a few seconds to appear, and if it still isn't there step back and try again."""
    for attempt in range(1, 4):
        ui = walk_until("w", "play_ui", timeout=PORTAL_WALK_SECS)
        if not ui:
            log(f"Walked {PORTAL_WALK_SECS:g}s without seeing the Play screen - standing still to let it show (lag?).")
            ui = wait_until(lambda: find("play_ui"), timeout=6)
        if ui:
            return ui
        log(f"The Play screen didn't show (try {attempt}/3).")
        save_debug("portal_walk")
        if attempt < 3:
            hold_key("s", PORTAL_WALK_SECS)   # back out exactly as far as we walked, so each retry starts from the spawn spot
            sleep(0.5)
    return None


PORTAL_TRIES = 3


def leave_portal_screen():
    """The Play screen is covering the hub (so the Areas menu can't be found): walk forward a little, which closes it and
    shows the main screen again. Returns True once the hub is visible."""
    for _ in range(3):
        if find("hub_ready") and not find("play_ui"):
            return True
        log("The Play screen is still open - walking forward to get back to the main screen.")
        hold_key("w", 1.0)
        sleep(1.0)
    return bool(find("hub_ready"))


def open_portal_ui():
    """Areas -> Portal -> walk in until the Play screen shows. If anything goes wrong (most often lag: Areas didn't
    open, or the Play screen never appeared) go back to the main screen, reopen Areas, press the Portal and walk again,
    up to PORTAL_TRIES times. Returns the Play screen's position or None."""
    for attempt in range(1, PORTAL_TRIES + 1):
        if attempt > 1:
            log(f"Lag probably got in the way - starting the portal trip again ({attempt}/{PORTAL_TRIES}).")
            activity("Portal trip again (lag?)")
            if find("play_ui"):                          # the Play screen is open already: no need to walk again
                return find("play_ui")
            leave_portal_screen()
        if not go_to_area("portal"):
            log("Couldn't open Areas / the Portal card.")
            continue
        sleep(3)  # teleporting there
        ui = reach_play_ui()                             # walks forward (briefly) until the Play screen pops up
        if ui:
            return ui
        log("The Play screen never popped up after walking forward.")
    log(f"Gave up on the portal after {PORTAL_TRIES} tries.")
    return None


def find_play_button(screen):
    """The big green PLAY! button at the bottom right of the portal panel, found by colour and shape (a wide,
    solid lime-green rounded bar). Used when the portal_play picture doesn't match (the button pulses, and at
    1920x1080 its size and edges differ from the saved picture). Other green things on that screen are small
    (CLEARED! badges, ticks) or hollow (the selected card's border), so they don't pass the size/fill test."""
    hsv = hsv_of(screen)
    mask = cv2.inRange(hsv, (38, 90, 140), (88, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 9)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    H = screen.shape[0]
    best = None
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        fill = cv2.contourArea(c) / float(w * h or 1)
        if 150 <= w <= 340 and 55 <= h <= 135 and 1.7 <= w / h <= 4.3 and fill >= 0.5 and y + h >= 0.55 * H:
            if best is None or w * h > best[0]:
                best = (w * h, x + w // 2, y + h // 2)
    return (best[1], best[2]) if best else None


def find_portal_play(screen=None):
    """The Play button: the saved picture first (two strictness levels), then the colour finder."""
    screen = screen if screen is not None else screenshot()
    with pinned_frame(screen):
        return find("portal_play", screen, 0.85) or find("portal_play", screen, 0.72) or find_play_button(screen)


def map_name(world, act):
    """Name the saved unit placements use: the Nightosphere farm keeps its old name, Wish Run is one map."""
    if act == "wish":
        return "wish"
    if (world, act) == ("nightosphere", "infinite"):
        return "nightosphere"
    return world if act == "infinite" else f"{world}_act{act}"


def match_kind(act):
    return act if is_endless(act) else "act"


def run_portal(world, act):
    """Portal -> pick a world -> pick Infinite Mode or an act -> Play -> Start, then play the match.
    The Nightosphere farm is run_portal("nightosphere", "infinite")."""
    global current_map
    world, act = norm_world(world), norm_act(act)
    if act == "wish":
        world = "candy"       # Wish Run is the same map whatever the world: always use the Candy Kingdom card
    if world not in WORLD_NAMES:
        stop_reason("templates", f"Unknown world '{world}'. Use: " + ", ".join(WORLD_NAMES))
        return "stop"
    if not is_endless(act) and act not in ("1", "2", "3", "4", "5"):
        stop_reason("templates", f"Unknown act '{act}'. Use 1-5, infinite or wish.")
        return "stop"
    if not prepare():
        return False
    label = "Wish Run" if act == "wish" else \
        f"{WORLD_NAMES[world][0]} - " + ("Infinite" if act == "infinite" else f"Act {act}")
    # one saved set of unit placements per map: the Nightosphere farm keeps its old name
    current_map = map_name(world, act)
    activity(f"Heading to the Portal ({label})")
    need = portal_templates(world, act)
    missing = [n for n in need if template(n, required=False) is None]
    if missing:
        log(f"{label} still needs these images in the templates folder: " + ", ".join(f"{n}.png" for n in missing))
        stop_reason("templates", f"{label} is missing images: " + ", ".join(f"{n}.png" for n in missing))
        return "stop"
    ui = open_portal_ui()
    if not ui:
        return False
    sleep(1)

    if not pick_world(ui, world):
        log(f"Couldn't find {WORLD_NAMES[world][0]} in the world list.")
        save_debug("world_list")
        return False
    if not pick_stage(world, act):
        log(f"Couldn't find the {'Wish Run' if act == 'wish' else 'Infinite Mode' if act == 'infinite' else 'Act ' + act} card.")
        save_debug("stage_card")
        return False
    # The Play button is the proof that the stage is selected. If it doesn't show, click the stage card again.
    for attempt in range(1, 4):
        pos = wait_until(find_portal_play, timeout=8)
        if pos:
            break
        if attempt < 3:
            log(f"No Play button after picking {label}; picking it again ({attempt}/2).")
            if not pick_stage(world, act):
                break
    if not pos:
        log(f"Couldn't find the Play button (picture match {best_score('portal_play'):.2f}, and no green PLAY! bar on screen).")
        save_debug("portal_play")
        return False
    log(f"Pressing Play at ({pos[0]}, {pos[1]}).")
    click(*pos)
    sleep(1)
    pos = wait_for("portal_start", timeout=8, threshold=0.85) or wait_for("portal_start", timeout=8, threshold=0.75)
    if not pos:
        log(f"Couldn't find the portal_start button (best match {best_score('portal_start'):.2f}).")
        save_debug("portal_start")
        return False
    log("Pressing Start.")
    click(*pos)
    sleep(1)
    start_match()
    return bool(play_until_end(match_kind(act)))


def run_bugmilk():
    """Bug milk farm: Portal -> Nightosphere -> Infinite Mode -> Play -> Start, home at the target wave."""
    return run_portal("nightosphere", "infinite")


def run_world():
    """The World / Act adventure: whatever world and act are chosen in BMO's settings.
    After Play Again the next run is already loading (Start Wave screen), so there is no hub and no Portal to
    visit: wait for the map, place units, start the wave and play."""
    if replay_pending[0] and time.time() - replay_pending[0] < 180 and not is_endless(norm_act(PORTAL_ACT)):
        replay_pending[0] = 0.0
        global current_map
        w, a = norm_world(PORTAL_WORLD), norm_act(PORTAL_ACT)
        current_map = map_name(w, a)     # same name run_portal() uses for its saved placements
        log(f"Play Again started the next act run - placing units for '{current_map}' without revisiting the Portal.")
        activity("Next act run loading")
        start_match()
        return play_until_end("act")
    replay_pending[0] = 0.0
    return run_portal(PORTAL_WORLD, PORTAL_ACT)


# ---------------- routines (routines/*.json) ----------------
# A routine is a list of steps (see bmo_common.STEP_SCHEMA). The steps are small wrappers around the
# functions above, so the hard parts (matching, placing units, the match loop) stay in Python and only the
# menu-walking lives in data. Edit them in BMO > TOOLS > Routines.
class BreakLoop(Exception):
    """A 'break' step: leave the innermost repeat."""


class StopRoutine(Exception):
    """A 'stop' step: the macro should end (the reason was already sent to BMO)."""


ACTIONS = {}


def action(name):
    def register(fn):
        ACTIONS[name] = fn
        return fn
    return register


def _num(step, key, default):
    v = step.get(key)
    return default if v in (None, "") else v


def _threshold(step):
    return float(_num(step, "threshold", 0)) or None      # 0 / missing = the global threshold


@action("area")
def act_area(s, ctx):
    return bool(go_to_area(str(s["name"])))


@action("click_template")
def act_click_template(s, ctx):
    name, th = str(s["name"]), _threshold(s)
    timeout = float(_num(s, "timeout", MATCH_TIMEOUT))
    scroll, page, anchor = int(_num(s, "scroll", 0)), str(s.get("page") or ""), str(s.get("anchor") or "")
    pos = None
    if scroll or page:                                    # look first, then scroll / page until it shows up
        pos = find(name, threshold=th)
        for _ in range(max(1, int(_num(s, "tries", 8)))):
            if pos:
                break
            if scroll:
                a = find(anchor) if anchor else None
                if a:                                     # the wheel only works with the cursor over the list
                    hx, hy = a[0] + int(_num(s, "anchor_dx", 0)), a[1] + int(_num(s, "anchor_dy", 0))
                    pydirectinput.moveTo(hx - 20, hy)
                    sleep(0.1)
                    pydirectinput.moveTo(hx, hy)
                    sleep(0.2)
                scroll_down(scroll)
                sleep(0.6)
            if page:
                arrow = find(page)
                if arrow:
                    click(*arrow)
                    sleep(0.8)
            pos = find(name, threshold=th)
    if not pos:
        pos = wait_for(name, timeout=timeout, threshold=th)
    if not pos:
        log(f"Routine: couldn't find {name}.")
        save_debug(f"routine_{name}")
        return False
    click(pos[0] + int(_num(s, "dx", 0)), pos[1] + int(_num(s, "dy", 0)))
    sleep(float(_num(s, "after", 0.5)))
    return True


@action("wait_template")
def act_wait_template(s, ctx):
    name, th = str(s["name"]), _threshold(s)
    timeout = float(_num(s, "timeout", MATCH_TIMEOUT))
    if s.get("gone"):
        ok = wait_until(lambda: True if find(name, threshold=th) is None else None, timeout) is not None
        if not ok:
            log(f"Routine: {name} never went away.")
        return ok
    ok = wait_for(name, timeout=timeout, threshold=th) is not None
    if not ok:
        log(f"Routine: {name} never showed up.")
        save_debug(f"routine_{name}")
    return ok


@action("press")
def act_press(s, ctx):
    press(str(s["key"]))
    return True


@action("hold_key")
def act_hold_key(s, ctx):
    key, until = str(s["key"]), str(s["until"])
    pydirectinput.keyDown(key)
    try:
        pos = wait_for(until, timeout=float(_num(s, "timeout", 25)))
    finally:
        pydirectinput.keyUp(key)
    if not pos:
        log(f"Routine: held {key} but {until} never showed up.")
        save_debug(f"routine_{until}")
    return bool(pos)


@action("sleep")
def act_sleep(s, ctx):
    sleep(float(s["seconds"]))
    return True


@action("scroll")
def act_scroll(s, ctx):
    scroll_down(int(_num(s, "clicks", 3)))
    sleep(0.4)
    return True


@action("click_at")
def act_click_at(s, ctx):
    ox = oy = 0
    if str(s.get("rel") or "window") == "window":
        rect = roblox_rect()
        if rect:
            ox, oy = int(rect[0]), int(rect[1])
    click(ox + int(s["x"]), oy + int(s["y"]))
    return True


def _branch(s, ctx, cond):
    steps = s.get("then" if cond else "else") or []
    return run_steps(steps, ctx) if steps else True


@action("if_template")
def act_if_template(s, ctx):
    return _branch(s, ctx, find(str(s["name"]), threshold=_threshold(s)) is not None)


@action("if_result")
def act_if_result(s, ctx):
    return _branch(s, ctx, ctx.get("result") == s.get("is"))


@action("repeat")
def act_repeat(s, ctx):
    forever = bool(s.get("forever"))
    times = max(1, int(_num(s, "times", 1)))
    steps, i = s.get("steps") or [], 0
    while forever or i < times:
        i += 1
        try:
            if not run_steps(steps, ctx):
                return False
        except BreakLoop:
            break
        if finish_flag.is_set() or (pending_mode[0] and pending_mode[0] != ctx.get("key")):
            break                                         # BMO asked us to finish / switch: honour it between loops
        if not steps:
            sleep(0.5)
    return True


@action("break")
def act_break(s, ctx):
    raise BreakLoop


@action("play_match")
def act_play_match(s, ctx):
    kind = str(s.get("kind") or ctx["routine"].get("kind") or "challenge")
    if kind not in MATCH_TIMEOUTS:
        kind = "challenge"
    if s.get("start", True):
        start_match()                                     # waits for the map, places units, presses Start Wave
    result = play_until_end(kind)
    ctx["result"] = ("victory" if result == "victory" else "ended" if result == "ended"
                     else "done" if result else "failed")
    return ctx["result"] != "failed"


@action("go_home")
def act_go_home(s, ctx):
    return bool(go_home_any())          # Return to Hub, or the Lobby button (either look) + Go Home


@action("set_map")
def act_set_map(s, ctx):
    global current_map
    current_map = str(s["name"])
    return True


@action("dismiss_daily")
def act_dismiss_daily(s, ctx):
    dismiss_daily_rewards(wait=float(_num(s, "wait", 0)))
    return True


@action("rejoin")
def act_rejoin(s, ctx):
    return bool(recover(max_wait=int(_num(s, "max_wait", 120))))


@action("log")
def act_log(s, ctx):
    log(str(s["text"]))
    return True


@action("activity")
def act_activity(s, ctx):
    activity(str(s["text"]))
    return True


@action("run")
def act_run(s, ctx):
    sub = ROUTINES.get(str(s["routine"]))
    if sub is None:
        log(f"Routine: there is no routine called {s['routine']}.")
        return False
    if ctx.get("depth", 0) >= 5:
        log("Routines are nested too deeply (a routine runs itself?).")
        return False
    inner = dict(ctx, routine=sub, depth=ctx.get("depth", 0) + 1)
    ok = run_steps(sub["steps"], inner)
    ctx["result"] = inner.get("result")
    return ok


@action("count_run")
def act_count_run(s, ctx):
    record_completed_run(ctx.get("key", "routine"))
    return True


@action("finish")
def act_finish(s, ctx):
    request_finish(str(s.get("reason") or "the routine asked to finish"))
    return True


@action("stop")
def act_stop(s, ctx):
    reason = str(s.get("reason") or "the routine asked to stop")
    log("Routine stop: " + reason)
    stop_reason("routine", reason)
    raise StopRoutine


def run_steps(steps, ctx):
    for step in steps:
        control_checkpoint()
        if not run_step(step, ctx):
            return False
    return True


def run_step(step, ctx):
    fn = ACTIONS.get(step.get("do"))
    if fn is None:
        log(f"Routine: unknown step {step.get('do')!r}.")
        return False
    if step.get("label"):
        activity(str(step["label"]))
    tries = 1 + max(0, int(_num(step, "retries", 0)))
    for attempt in range(1, tries + 1):
        if fn(step, ctx) is not False:
            return True
        if attempt < tries:
            log(f"Routine: {step.get('do')} failed, trying again ({attempt}/{tries - 1}).")
            sleep(1)
    return bool(step.get("optional"))                     # an optional step that fails doesn't fail the run


def run_routine(routine):
    """One pass of a routine = one run. Returns True / False like the built-in adventures, or "stop"."""
    global current_map
    problems = [t for lvl, t in common.validate_routine(routine, TEMPLATES, tuple(ROUTINES)) if lvl == "bad"]
    if problems:
        for t in problems[:6]:
            log("Routine problem: " + t)
        stop_reason("templates", f"Routine '{routine['name']}' can't run: {problems[0]}")
        return "stop"
    if routine.get("map"):
        current_map = str(routine["map"])
    activity(f"Running {routine['name']}")
    if routine.get("prepare", True) and not prepare():
        return False
    ctx = {"routine": routine, "key": routine["key"], "result": None, "depth": 0}
    try:
        return bool(run_steps(routine["steps"], ctx))
    except BreakLoop:
        return True
    except StopRoutine:
        return "stop"


BUILTIN_RUNNERS = {"challenges": run_challenge, "bugmilk": run_world, "raid": run_raid, "world": run_world}
MATCH_KINDS = common.MATCH_KINDS
MODES = {
    "1": ("challenges", "Challenges (Dungeon Train)", run_challenge),
    "2": ("raid", "Lich Raid", run_raid),
    "3": ("world", "World / Act / Endless", run_world),
}


def runner_for(key):
    """The function that plays one run of an adventure (built-in or a routine), or None."""
    if key in BUILTIN_RUNNERS:
        return BUILTIN_RUNNERS[key]
    r = ROUTINES.get(key)
    return functools.partial(run_routine, r) if r else None


def counts_automatically(key):
    """The raid counts its own victories; a routine can opt out with "auto_count": false and use count_run."""
    if key == "raid":
        return False
    return ROUTINES.get(key, {}).get("auto_count", True) is not False


def choose_mode():
    """Returns an adventure key: challenges / bugmilk / raid / a routine's file name."""
    options = [(num, key, title) for num, (key, title, _fn) in MODES.items()]
    for i, (key, r) in enumerate(ROUTINES.items(), len(MODES) + 1):
        options.append((str(i), key, f"{r['name']} (routine)"))
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        for num, key, _title in options:
            if arg in (num, key):
                return key
    print("\nWhich macro do you want to run?")
    for num, _key, title in options:
        print(f"  {num}) {title}")
    while True:
        choice = input("Type a number and press Enter: ").strip()
        for num, key, _title in options:
            if choice == num:
                return key


def join_running_match(key):
    """Join an active match or the Start Wave screen reached through Play Again."""
    screen = screenshot()
    if key == "world" and not is_endless(norm_act(PORTAL_ACT)) and (find("start_match", screen, 0.75) or find_wave_button(screen)):
        log("Play Again opened the next act run - preparing it without revisiting the Portal.")
        start_match()
        return play_until_end("act")
    if read_wave(screen) is None:
        return None
    log("Already inside a match - joining it.")
    activity("Joined a match in progress")
    if key == "raid":
        return lich_battles(first_start=False)
    if key in ROUTINES:
        kind = ROUTINES[key].get("kind") or "challenge"
        return bool(play_until_end(kind if kind in MATCH_TIMEOUTS else "challenge"))
    if key == "world":
        return play_until_end(match_kind(norm_act(PORTAL_ACT)))
    return play_until_end("infinite" if key == "bugmilk" else "challenge")


def unstick_recovery():
    """BMO's watchdog saw no progress for minutes. Save what we see, then get back to a known place.
    Returns True when a match is on screen (the caller should rejoin it instead of walking to the hub)."""
    unstick_flag.clear()
    log("Watchdog recovery: saving a screenshot and checking where we are.")
    activity("Watchdog recovery")
    in_match = False
    try:
        save_debug("stuck")
        screen = screenshot()
        if find("start_match", screen, 0.75):
            log("Start Wave is still showing - pressing it.")
            click_start_wave()
            in_match = True
        elif read_wave(screen) is not None or find("heroes_title", screen):
            in_match = True         # the wave counter or the Units panel only exist inside a match
        elif not find("hub_ready", screen):
            recover(max_wait=120)
    finally:
        unstick_flag.clear()
    return in_match


# ---------------- setup check (python macro.py --check) ----------------
REQUIRED_TEMPLATES = (
    ("Every mode", ("hub_ready", "daily_title", "daily_claim", "daily_close", "heroes_title", "start_match",
                    "return_hub", "challenge_popup", "toast_ok", "toast_bad")),
    ("Reconnecting", ("reconnect", "play_button", "game_tile", "favorites_item", "menu_icon")),
    ("Dungeon Train", ("card_dungeon", "take_challenge", "play", "bonus_claimed", "map_candy", "map_ice")),
    ("World / Act navigation", ("card_portal", "play_ui", "portal_play", "portal_start", "arrow_right",
                                 "act_endless", "act_play_again", "return_hub", "wave32")),
    ("Wish Run", ("candy_world", "act_wish", "wish_it")),
    ("Lich raid", ("items", "your_items", "lich_portal_icon", "open_lich_portal", "lich_enter", "open_lich",
                   "next_btn", "yes", "none_left", "back", "return_hub")),
    ("Going home (any one of these)", ("return_hub", "lobby", "lobby_2", "go_home")),
)
SAVED_PLACEMENTS = (("candy", "Dungeon Train"), ("ice", "Dungeon Train"),
                    ("nightosphere", "Nightosphere Endless"), ("lich", "Lich raid"))


def setup_check():
    """Print a checklist (one '@BMO check' event per line) for BMO's CHECK button, then exit."""
    def item(level, text):
        event("check", level=level, text=text)
        log({"ok": "[OK]", "warn": "[!!]", "bad": "[XX]"}[level] + " " + text)

    # templates
    if not TEMPLATES.is_dir():
        item("bad", f"No templates folder at {TEMPLATES}")
    else:
        for group, names in REQUIRED_TEMPLATES:
            missing, broken = [], []
            for n in names:
                f = TEMPLATES / f"{n}.png"
                if not f.exists():
                    missing.append(n)
                elif cv2.imread(str(f)) is None:
                    broken.append(n)
            if missing or broken:
                bits = ([f"missing: {', '.join(missing)}"] if missing else []) + \
                       ([f"unreadable: {', '.join(broken)}"] if broken else [])
                item("bad", f"Templates ({group}) " + "; ".join(bits))
            else:
                item("ok", f"Templates ({group}): all {len(names)} found")

    # saved unit placements (a missing one makes the macro stop and wait for you to record it)
    for name, mode in SAVED_PLACEMENTS:
        if placement_path(name).exists():
            item("ok", f"Placements for {name} saved ({placement_path(name).name})")
        else:
            item("warn", f"No saved placements for {name} ({mode}): the macro will wait for you to record them ({record_key_label()})")

    # displays + Roblox window
    try:
        with _MSS() as sct:
            mons = list(sct.monitors)
    except Exception as e:
        item("bad", f"Couldn't read the displays: {e}")
        mons = []
    if len(mons) > 1:
        m = mons[1]
        item("ok", f"The macro captures display 1 ({m['width']}x{m['height']} at {m['left']},{m['top']})"
                   + (f"; {len(mons) - 2} other display(s) are ignored" if len(mons) > 2 else ""))
    _rect_cache["t"] = 0.0
    _scale_cache["t"] = 0.0
    rect = real_rect()
    if not rect:
        item("warn", "Couldn't find a window titled Roblox (is pygetwindow installed? In fullscreen the macro assumes Roblox fills display 1)")
    else:
        ww, wh = rect[2] - rect[0], rect[3] - rect[1]
        if len(mons) > 1:
            m = mons[1]
            cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                item("ok", "Roblox is on display 1")
            else:
                item("bad", "Roblox is NOT on display 1, so the macro can't see or click it")
        sc = ui_scale()
        supported = supported_resolution(ww, wh)
        if supported:
            item("ok", f"Supported: Roblox is {supported} (window {ww}x{wh}, auto-scale x{sc:.2f})")
        else:
            item("warn", f"Unsupported, may not work: Roblox window is {ww}x{wh}. "
                         f"Use {' or '.join(SUPPORTED_RESOLUTIONS_TEXT)} on your main display.")
        n_hints = len(_scale_hint)
        if abs(sc - 1.0) >= 0.01 and supported:
            item("ok", f"{n_hints} picture sizes already learned for this resolution" if n_hints
                       else "No learned picture sizes yet for this resolution: the first run searches, later runs are faster")
    if RES_LOCK_ENABLED:
        if rect:
            label = save_res_profile(rect[2] - rect[0], rect[3] - rect[1])
            item("ok", f"Resolution locked: {label}. BMO will use this on every Start (skipping the picture-size search) "
                       "until you run CHECK SETUP again, so run it again whenever you change resolution")
        else:
            item("warn", "Resolution not saved because the Roblox window wasn't found. Open Roblox and run CHECK SETUP again")
    pct = windows_display_scale()
    if pct and pct != 100:
        item("warn", f"Windows display scale is {pct}%. Set Settings > Display > Scale to 100% for the most reliable matching")
    # pop-up handlers (they only act if their picture exists)
    for it in load_interrupts():
        t = str(it["template"])
        if has_template(t):
            item("ok", f"Pop-up handler '{it['name']}' is active")
        else:
            item("warn", f"Pop-up handler '{it['name']}' is off: templates/{t}.png is missing")
    # World / Act adventure: only the pictures for the world and act chosen in BMO's settings
    w, a = norm_world(PORTAL_WORLD), norm_act(PORTAL_ACT)
    if w in WORLD_NAMES:
        miss = [n for n in portal_templates(w, a) if template(n, required=False) is None]
        what = "Wish Run" if a == "wish" else f"{WORLD_NAMES[w][0]} / {'Infinite' if a == 'infinite' else 'Act ' + a}"
        if miss:
            item("warn", f"World / Act ({what}) needs: " + ", ".join(f"{n}.png" for n in miss))
        else:
            item("ok", f"World / Act ({what}): all pictures found")
    # routines (routines/*.json)
    for fname, problem in ROUTINE_ERRORS:
        item("bad", f"routines/{fname} can't be read: {problem}")
    for key, r in ROUTINES.items():
        bad = [t for lvl, t in common.validate_routine(r, TEMPLATES, tuple(ROUTINES)) if lvl == "bad"]
        if bad:
            item("bad", f"Routine '{r['name']}': " + bad[0] + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""))
        else:
            item("ok", f"Routine '{r['name']}': {len(r['steps'])} steps, all pictures found")
    event("check_done")


# ---------------- one-shot tools for BMO (python macro.py --test-templates / --grab / --snap / --step) ----------------
TEMPLATE_NEEDS = {"reconnect": 0.85, "play_button": 0.85, "game_tile": 0.85, "favorites_item": 0.85,
                  "menu_icon": 0.85, "bonus_claimed": 0.75, "portal_play": 0.85, "portal_start": 0.85,
                  "start_match": 0.85, "wave32": 0.80}


def template_need(name):
    """The score the macro asks of this picture (see where it is used), for the template tester."""
    if name in TEMPLATE_NEEDS:
        return TEMPLATE_NEEDS[name]
    if name.startswith("toast_"):
        return TOAST_THRESHOLD
    if name.startswith(("card_", "daily_")):
        return 0.80
    if name.startswith("map_"):
        return 0.85
    return THRESHOLD


def best_match(name, screen):
    """(score, (left, top), (width, height)) of the best place for a picture, found the way the macro finds
    it (several sizes in grey for pop-ups and area cards, one size in colour for the rest), or None if the
    file is missing."""
    tpl = template(name, required=False)
    if tpl is None:
        return None
    th, tw = tpl.shape[:2]
    if name in MULTISCALE or name.startswith("card_"):
        gray, tg = gray_of(screen), cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        best = (0.0, (0, 0), (tw, th))
        for sc in SCALES:
            t = cv2.resize(tg, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
            if t.shape[0] >= gray.shape[0] or t.shape[1] >= gray.shape[1]:
                continue
            _, score, _, loc = cv2.minMaxLoc(match(gray, t))
            if score > best[0]:
                best = (float(score), (int(loc[0]), int(loc[1])), (t.shape[1], t.shape[0]))
        return best
    if th >= screen.shape[0] or tw >= screen.shape[1]:
        return (0.0, (0, 0), (tw, th))
    score, f, centre = sweep_find(name, screen, 2.0)
    if centre is None:
        return (0.0, (0, 0), (tw, th))
    w, h = int(round(tw * f)), int(round(th * f))
    return (float(score), (centre[0] - w // 2, centre[1] - h // 2), (w, h))


def test_templates(names):
    """Score pictures against what is on screen right now. One @BMO tpl event per picture; with a single
    picture it also saves debug/tpl_test.png with the best spot boxed, for BMO's template tester."""
    names = names or sorted(p.stem for p in TEMPLATES.glob("*.png"))
    screen = screenshot()
    image = None
    for name in names:
        res = best_match(name, screen)
        need = template_need(name)
        if res is None:
            event("tpl", name=name, score=0.0, need=need, found=False, missing=True)
            continue
        score, (x, y), (w, h) = res
        found = score >= need
        event("tpl", name=name, score=round(score, 3), need=need, found=found, box=[x, y, x + w, y + h])
        if len(names) == 1:
            image = screen.copy()
            color = (60, 190, 60) if found else (60, 60, 230)
            cv2.rectangle(image, (x - 3, y - 3), (x + w + 3, y + h + 3), color, 3)
            cv2.putText(image, f"{name} {score:.2f} (needs {need:.2f})", (max(0, x), max(20, y - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    path = None
    if image is not None:
        crop, _ = roblox_crop(image)
        h, w = crop.shape[:2]
        if w > 760:
            crop = cv2.resize(crop, (760, max(1, int(h * 760 / w))), interpolation=cv2.INTER_AREA)
        path = write_image(DEBUG / "tpl_test.png", crop, ".png")
    event("tpl_done", image=str(path) if path else None)


def grab_for_capture():
    """The Roblox window at full size, saved for BMO's template capture tool (debug/grab.png)."""
    crop, (ox, oy) = roblox_crop(screenshot())
    path = write_image(DEBUG / "grab.png", crop, ".png")
    event("grab", path=str(path) if path else None, w=int(crop.shape[1]), h=int(crop.shape[0]),
          res=capture_res_name(),
          window=bool(roblox_rect()))


def run_single_step(raw):
    """Run one routine step (or a list of them) right now, so it can be tried out from BMO's editor."""
    import json as _json
    steps = _json.loads(raw)
    steps = [steps] if isinstance(steps, dict) else steps
    problems = [t for lvl, t in common.validate_steps(steps, TEMPLATES, tuple(ROUTINES)) if lvl == "bad"]
    if problems:
        for t in problems[:4]:
            log("Can't test: " + t)
        event("step_done", ok=False)
        return
    focus_roblox()
    ctx = {"routine": {"key": "test", "name": "test", "steps": steps}, "key": "test", "result": None, "depth": 1}
    try:
        ok = run_steps(steps, ctx)
    except BreakLoop:
        ok = True
    except StopRoutine:
        ok = False
    except pydirectinput.FailSafeException:
        log("Failsafe triggered (mouse in the corner).")
        ok = False
    log("Step finished: " + ("it worked." if ok else "it did NOT work."))
    event("step_done", ok=bool(ok))


# ---------------- main loop ----------------
def main():
    global completed_runs
    key = choose_mode()
    run = runner_for(key)
    event("mode", mode=key)
    if PLAN:
        plan_event()
        log("Plan: " + " > ".join(f"{m} x{n}" if n else f"{m} (no limit)" for m, n in PLAN)
            + (" (repeats)" if PLAN_LOOP else ""))
    locked = prime_res_profile()
    warm = None
    if locked:
        warm = threading.Thread(target=warm_templates, daemon=True)
        warm.start()                      # loads the pictures while the countdown ticks
    delay = max(0.0, START_DELAY)
    activity(f"Starting in {delay:g} seconds" if delay else "Starting")
    log(f"Starting in {delay:g} seconds. Click into Roblox. Use BMO to pause, resume, or stop." if delay
        else "Starting now. Use BMO to pause, resume, or stop.")
    threading.Thread(target=control_reader, daemon=True).start()
    try:
        if delay:
            sleep(delay)
    except KeyboardInterrupt:
        log("Stopped during startup.")
        return
    for fname, problem in ROUTINE_ERRORS:
        log(f"Couldn't read routines/{fname}: {problem}")
    if warm is not None:
        warm.join(timeout=30)
    confirm_res_profile()
    display_report()
    memory_report("Memory at start")
    focus_roblox()
    keep_awake(True)
    guard_ready[0] = True                 # from here on, input is held whenever Roblox isn't the active window
    failures = 0
    completed_runs = 0
    memory_failures = 0
    crashes = 0
    first_pass = True
    run_key = key
    try:
        while True:
            if finish_flag.is_set():
                log("Finished - stopping as requested.")
                break
            queued_key = pending_mode[0]
            pending_mode[0] = None
            queued = runner_for(queued_key) if queued_key else None
            if queued is not None and queued_key != run_key:
                run_key, run = queued_key, queued
                failures = 0
                event("mode", mode=run_key)
                log(f"Switched mode to {run_key}.")
            try:
                ok = None
                if first_pass:
                    first_pass = False
                    ok = run_guarded(join_running_match, run_key)
                if ok is None:
                    ok = run_guarded(run)
            except Unstick:
                if unstick_recovery():
                    first_pass = True   # a match is on screen: rejoin it instead of walking to the hub
                continue
            except (cv2.error, MemoryError) as e:
                if not is_memory_error(e):
                    raise
                memory_failures += 1
                gc.collect()
                memory_report(f"Out of memory ({memory_failures} in a row)")
                if memory_failures >= 10:
                    log("The PC keeps running out of memory, stopping. Close other programs (or restart Roblox) and start me again.")
                    stop_reason("memory", "The PC keeps running out of memory.")
                    break
                log("Waiting 30 seconds for memory to free up, then trying again.")
                sleep(30)
                continue
            except pydirectinput.FailSafeException:
                raise
            except Frozen:
                log(f"The game screen hasn't changed for {FREEZE_SECS:g} seconds - Roblox looks frozen.")
                activity("Game frozen - trying to recover")
                save_debug("frozen")
                if not recover(max_wait=90):
                    log("Roblox is frozen and I can't get back in, stopping. Close and reopen the game, then start me again.")
                    stop_reason("frozen", "The game froze (the screen stopped changing) and I couldn't recover.")
                    break
                continue
            except Disconnected:
                log("Disconnected or kicked from the game.")
                activity("Disconnected - rejoining the game")
                if not recover():
                    log("Couldn't get back in, stopping.")
                    stop_reason("disconnect", "Disconnected and couldn't get back into the game.")
                    break
                continue
            except Exception as e:  # screen grab failed, a button wasn't where expected, ...
                import traceback
                crashes += 1
                log(f"Unexpected error ({crashes} in a row): {type(e).__name__}: {e}")
                traceback.print_exc()
                if crashes >= 5:
                    log("Too many unexpected errors in a row, stopping.")
                    stop_reason("crashes", f"Too many unexpected errors in a row (last: {type(e).__name__}: {e}).")
                    break
                sleep(10)
                continue
            crashes = 0
            memory_failures = 0
            if ok == "stop":
                break
            if ok == "switch":      # left the raid because BMO queued another mode; the loop top applies it
                failures = 0
                continue
            if ok == "challenges":  # out of Lich Portals: fall back to the challenge farm (the loop top applies a queued plan step first)
                run_key, run = "challenges", run_challenge
                event("mode", mode="challenges")
                activity("Switching to challenges")
                failures = 0
                continue
            if ok:
                if counts_automatically(run_key):
                    record_completed_run(run_key)
                failures = 0
                if completed_runs % 5 == 0 and completed_runs:
                    memory_report()
                if finish_flag.is_set():
                    log("Finished the run - stopping as requested.")
                    break
            else:
                if finish_flag.is_set():
                    log("That run failed, and BMO asked me to finish - stopping.")
                    break
                failures += 1
                event("failure", streak=failures, mode=run_key)
                log(f"Run failed ({failures} in a row). Retrying...")
                try:
                    save_debug(f"failed_{run_key}")     # what the screen looked like when the run gave up
                except Exception:
                    pass
                if failures >= 5:
                    log("Too many failures in a row, stopping.")
                    stop_reason("failures", "Too many failed runs in a row.")
                    break
                if not find("hub_ready"):
                    recover(max_wait=20)  # maybe we got dumped to the Roblox home screen
                sleep(5)
    except KeyboardInterrupt:
        pass
    except pydirectinput.FailSafeException:
        log("Failsafe triggered (mouse in the corner).")
        stop_reason("failsafe", "Failsafe triggered (mouse in the top-left corner).")
    finally:
        guard_ready[0] = False
        keep_awake(False)
    log("Stopped.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--check" in args:
        setup_check()
    elif "--test-templates" in args:
        test_templates([a for a in args[args.index("--test-templates") + 1:] if not a.startswith("--")])
    elif "--scan" in args:
        names = [a for a in args[args.index("--scan") + 1:] if not a.startswith("--")] \
            or ["take_challenge", "raid_prompt", "play", "start_match", "upgrade_all", "hub_ready", "menu_icon"]
        screen = screenshot()
        rect = roblox_rect()
        print(f"Screen grab {screen.shape[1]}x{screen.shape[0]} (virtual), auto-scale x{ui_scale():.2f}, "
              f"Roblox window (virtual) {rect}")
        for n in names:
            res = best_match(n, screen)
            if res is None:
                print(f"  {n:16} missing templates/{n}.png")
            else:
                score, (x, y), (w, h) = res
                print(f"  {n:16} best score {score:.2f} at ({x},{y}), saved size {template(n).shape[1]}x{template(n).shape[0]}"
                      f", best fit {w}x{h} = {w / max(1, template(n).shape[1]):.2f}x")
        DEBUG.mkdir(exist_ok=True)
        cv2.imwrite(str(DEBUG / "scan.png"), screen)
        print(f"Saved what I see to {DEBUG / 'scan.png'}")
    elif "--grab" in args:
        grab_for_capture()
    elif "--snap" in args:
        do_snapshot()
    elif "--step" in args and args.index("--step") + 1 < len(args):
        run_single_step(args[args.index("--step") + 1])
    else:
        main()
