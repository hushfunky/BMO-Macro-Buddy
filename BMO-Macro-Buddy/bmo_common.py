"""Shared by macro.py and the BMO launcher (standard library only, so both can import it).

Keep this file beside macro.py and adventure_time_launcher.py.

It holds the things that used to be copy-pasted in both programs (the list of adventures, the plan
parser, the event prefix) plus the routine system: small JSON files in routines/ that describe an
adventure as a list of steps, so a new event or a changed menu is a data edit instead of a code edit.
"""
import json
import os
import re
from pathlib import Path

EVENT_PREFIX = "@BMO "

# The adventures that are written in Python inside macro.py.
BUILTIN = [
    {"key": "challenges", "label": "DUNGEON TRAIN", "short": "CHALLENGES",
     "aliases": ["challenge", "dungeon", "train", "dungeon train"]},
    {"key": "raid", "label": "LICH RAID", "short": "RAIDS",
     "aliases": ["raids", "lich", "lich raid", "lich portal"]},
    {"key": "world", "label": "WORLD / ACT / ENDLESS", "short": "WORLDS",
     "aliases": ["act", "acts", "stage", "stages", "portal", "world act", "nightosphere", "bugmilk", "bug milk", "farm"]},
]
BUILTIN_KEYS = [a["key"] for a in BUILTIN]
MATCH_KINDS = ("challenge", "infinite", "raid", "wish")


# ---------------------------------------------------------------- safe file helpers
def write_text_atomic(path, text, encoding="utf-8"):
    """Write to a temp file, then swap it in. A crash or power cut mid-write leaves the old file intact
    instead of a half-written one (which used to silently reset settings or break a saved placement)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)


def write_json_atomic(path, data, **kwargs):
    write_text_atomic(path, json.dumps(data, **kwargs))


def read_json(path, default=None):
    """Parsed JSON, or `default` if the file is missing. A damaged file is kept as <name>.bad (so nothing is
    lost) and `default` is returned, rather than the program quietly carrying on with a blank slate."""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError:
        try:
            os.replace(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return default
    except OSError:
        return default


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def norm(text):
    return re.sub(r"[\s_\-]+", " ", str(text).lower()).strip()


# ---------------------------------------------------------------- routines (routines/*.json)
def load_routines(here):
    """routines/*.json -> ({key: routine}, [(file name, problem)]). Files starting with _ are ignored."""
    routines, errors = {}, []
    folder = Path(here) / "routines"
    if not folder.is_dir():
        return routines, errors
    reserved = set(BUILTIN_KEYS) | {norm(n) for a in BUILTIN for n in a["aliases"]}
    for p in sorted(folder.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
                raise ValueError("a routine is an object with a 'steps' list")
        except Exception as e:
            errors.append((p.name, str(e)[:120]))
            continue
        key = slug(p.stem) or "routine"
        while key in routines or key in reserved:
            key += "_r"
        name = str(data.get("name") or p.stem)
        r = dict(data)
        r.update(key=key, file=str(p), name=name, steps=data["steps"],
                 label=str(data.get("label") or name).upper()[:26],
                 short=str(data.get("short") or name).upper()[:10])
        routines[key] = r
    return routines, errors


def build_adventures(routines):
    """Everything BMO can run: the built-in adventures, then one entry per routine file."""
    advs = [dict(a, routine=False) for a in BUILTIN]
    for key, r in routines.items():
        advs.append({"key": key, "label": r["label"], "short": r["short"], "routine": True,
                     "aliases": [r["name"], key, Path(r["file"]).stem]})
    return advs


def adventure_names(advs):
    names = {}
    for a in advs:
        for n in (a["key"], a["label"], a["short"], *a.get("aliases", [])):
            names.setdefault(norm(n), a["key"])
    return names


# ---------------------------------------------------------------- chained plans
_PART = re.compile(r"^(.+?)\s*(?:(?::\s*|[x\u00d7*])(\d+)|\s+(\d+))?$")


def parse_plan(text, advs):
    """"raid:5, challenges:3, bugmilk" -> ([("raid", 5), ("challenges", 3), ("bugmilk", 0)], [problems]).
    A count of 0 means "keep going until stopped"; only the last step may leave the count out."""
    names = adventure_names(advs)
    steps, bad = [], []
    parts = [p.strip() for p in re.split(r"[,;>\n]+", text or "") if p.strip()]
    for i, part in enumerate(parts):
        m = _PART.match(part.lower())
        key = names.get(norm(m.group(1))) if m else None
        # Older plans used a dedicated Bug Milk adventure; it is now Nightosphere Endless.
        if key is None and m and norm(m.group(1)) in ("bugmilk", "bug milk", "nightosphere", "farm"):
            key = "world"
        if key is None:
            bad.append(f"'{part}' isn't an adventure (try " + ", ".join(a["key"] for a in advs) + ")")
            continue
        count = int(m.group(2) or m.group(3) or 0)
        if count == 0 and i < len(parts) - 1:
            bad.append(f"'{part}' needs a count (like {key}:5) because more steps follow")
            continue
        steps.append((key, count))
    return steps, bad


def plan_text(steps, advs):
    label = {a["key"]: a["label"].title() for a in advs}
    return " > ".join(f"{label.get(k, k)} x{n}" if n else f"{label.get(k, k)} (no limit)" for k, n in steps)


# ---------------------------------------------------------------- routine step schema
def F(name, typ="str", default=None, required=False, help="", choices=None):
    return {"name": name, "type": typ, "default": default, "required": required, "help": help,
            "choices": choices}


# Fields every step understands.
COMMON_FIELDS = [
    F("label", "str", "", help="text BMO shows while this step runs"),
    F("optional", "bool", False, help="if this step fails, carry on instead of failing the run"),
    F("retries", "int", 0, help="extra tries (1 second apart) before the step counts as failed"),
]

STEP_SCHEMA = {
    "area": {
        "doc": "Open the Areas menu (M) and press Go on an area's card.",
        "fields": [F("name", "str", required=True,
                     help="card name: needs templates/card_<name>.png (dungeon, portal, vampire...)")]},
    "click_template": {
        "doc": "Wait for a picture to appear on screen, then click it. Can scroll or page while it looks.",
        "fields": [
            F("name", "template", required=True),
            F("timeout", "float", 15, help="seconds to wait for it"),
            F("threshold", "float", 0, help="0 = the global match threshold"),
            F("dx", "int", 0, help="click this many pixels right of the picture's centre"),
            F("dy", "int", 0, help="...and this many pixels below it"),
            F("anchor", "template", "", help="scrolling: hover near this picture while looking for 'name'"),
            F("anchor_dx", "int", 0, help="scrolling: hover this far right of the anchor"),
            F("anchor_dy", "int", 0, help="scrolling: hover this far below the anchor"),
            F("scroll", "int", 0, help="mouse-wheel clicks to try while 'name' isn't visible (positive = down)"),
            F("page", "template", "", help="a next-page arrow to click while 'name' isn't visible"),
            F("tries", "int", 8, help="how many scroll/page attempts"),
            F("after", "float", 0.5, help="seconds to wait after the click")]},
    "wait_template": {
        "doc": "Wait until a picture appears (or, with gone, until it disappears).",
        "fields": [F("name", "template", required=True), F("timeout", "float", 15),
                   F("threshold", "float", 0), F("gone", "bool", False, help="wait for it to go away instead")]},
    "press": {"doc": "Tap a keyboard key.", "fields": [F("key", "key", required=True)]},
    "hold_key": {
        "doc": "Hold a key down until a picture shows up (e.g. walk forward until a menu pops up).",
        "fields": [F("key", "key", required=True), F("until", "template", required=True),
                   F("timeout", "float", 25)]},
    "sleep": {"doc": "Wait.", "fields": [F("seconds", "float", 1, required=True)]},
    "scroll": {"doc": "Turn the mouse wheel where the cursor is.",
               "fields": [F("clicks", "int", 3, help="positive = down, negative = up")]},
    "click_at": {
        "doc": "Click a fixed spot. Prefer click_template; fixed spots break when the UI moves.",
        "fields": [F("x", "int", required=True), F("y", "int", required=True),
                   F("rel", "choice", "window", choices=["window", "screen"],
                     help="window = measured from the Roblox window's top-left")]},
    "if_template": {
        "doc": "Do one list of steps if a picture is on screen right now, another if not.",
        "fields": [F("name", "template", required=True), F("threshold", "float", 0),
                   F("then", "steps", [], help="steps to run if it is visible (JSON list)"),
                   F("else", "steps", [], help="steps to run if it is not (JSON list)")]},
    "if_result": {
        "doc": "Branch on how the last play_match ended.",
        "fields": [F("is", "choice", "victory", required=True, choices=["victory", "ended", "done", "failed"]),
                   F("then", "steps", []), F("else", "steps", [])]},
    "repeat": {
        "doc": "Run a list of steps several times (or until a 'break' step).",
        "fields": [F("times", "int", 1), F("forever", "bool", False, help="loop until break / finish / stop"),
                   F("steps", "steps", [], help="steps to repeat (JSON list)")]},
    "break": {"doc": "Leave the innermost repeat.", "fields": []},
    "play_match": {
        "doc": "Place units (if this map has placements), then play: Upgrade All, Start/Skip Wave, until the match ends.",
        "fields": [F("kind", "choice", "", choices=["", *MATCH_KINDS],
                     help="challenge = till the end screen, infinite = home at the target wave, raid = the Lich raid loop (Open Lich > Next > Yes), wish = infinite + picks wishes"),
                   F("start", "bool", True, help="false = the match is already running, don't place units")]},
    "go_home": {"doc": "Lobby button, then Go Home.", "fields": []},
    "set_map": {"doc": "Say which map this is (picks placements/<name>.json and zones).",
                "fields": [F("name", "str", required=True)]},
    "dismiss_daily": {"doc": "Close the Daily Rewards window if it is showing.",
                      "fields": [F("wait", "float", 0, help="seconds to keep looking for it")]},
    "rejoin": {"doc": "Get back into the game (Reconnect / Roblox home -> game -> Play).",
               "fields": [F("max_wait", "int", 120)]},
    "log": {"doc": "Write a line in BMO's log.", "fields": [F("text", "str", required=True)]},
    "activity": {"doc": "Change the activity text shown on BMO.", "fields": [F("text", "str", required=True)]},
    "run": {"doc": "Run another routine (by its file name) as a sub-routine.",
            "fields": [F("routine", "str", required=True)]},
    "count_run": {"doc": "Count one completed run (when the routine has \"auto_count\": false).", "fields": []},
    "finish": {"doc": "Stop cleanly after this run.", "fields": [F("reason", "str", "routine asked to finish")]},
    "stop": {"doc": "Stop the macro now and send an alert.",
             "fields": [F("reason", "str", "routine asked to stop")]},
}
NESTED = ("then", "else", "steps")


def step_templates(step):
    """Template names (without .png) a step needs."""
    do, out = step.get("do"), []
    if do == "area":
        out.append("card_" + str(step.get("name", "")))
    elif do == "click_template":
        out += [step.get("name"), step.get("anchor"), step.get("page")]
    elif do in ("wait_template", "if_template"):
        out.append(step.get("name"))
    elif do == "hold_key":
        out.append(step.get("until"))
    return [str(x) for x in out if x]


def validate_steps(steps, templates_dir, known_routines=(), where="", out=None):
    out = [] if out is None else out
    if not isinstance(steps, list):
        out.append(("bad", f"{where or 'steps'}: must be a list"))
        return out
    for i, step in enumerate(steps, 1):
        at = f"{where}step {i}"
        if not isinstance(step, dict):
            out.append(("bad", f"{at}: must be an object like {{\"do\": \"press\", \"key\": \"e\"}}"))
            continue
        do = step.get("do")
        spec = STEP_SCHEMA.get(do)
        if spec is None:
            out.append(("bad", f"{at}: unknown action {do!r} (known: {', '.join(STEP_SCHEMA)})"))
            continue
        at = f"{at} ({do})"
        for f in spec["fields"] + COMMON_FIELDS:
            v = step.get(f["name"])
            if v in (None, "") and f["required"]:
                out.append(("bad", f"{at}: needs '{f['name']}'"))
                continue
            if v is None:
                continue
            try:
                if f["type"] == "int":
                    int(v)
                elif f["type"] == "float":
                    float(v)
                elif f["type"] == "steps" and not isinstance(v, list):
                    raise ValueError
                elif f["type"] == "choice" and v not in (f["choices"] or ()):
                    out.append(("bad", f"{at}: '{f['name']}' must be one of {', '.join(map(repr, f['choices']))}"))
            except (TypeError, ValueError):
                out.append(("bad", f"{at}: '{f['name']}' has the wrong kind of value ({v!r})"))
        for t in step_templates(step):
            if not (Path(templates_dir) / f"{t}.png").exists():
                out.append(("bad", f"{at}: missing templates/{t}.png"))
        if do == "run" and step.get("routine") and step["routine"] not in known_routines:
            out.append(("bad", f"{at}: no routine called {step['routine']!r}"))
        for nest in NESTED:
            if nest in step and nest in [f["name"] for f in spec["fields"]]:
                validate_steps(step[nest], templates_dir, known_routines, f"{at} > {nest}: ", out)
    return out


def validate_routine(routine, templates_dir, known_routines=()):
    """[(level, text)] where level is 'bad' (will fail) or 'warn'. An empty list means it looks fine."""
    out = []
    steps = routine.get("steps")
    if not isinstance(steps, list) or not steps:
        return [("bad", "The routine has no steps.")]
    kind = routine.get("kind")
    if kind not in (None, "") and kind not in MATCH_KINDS:
        out.append(("bad", f"routine 'kind' must be one of {', '.join(MATCH_KINDS)}"))
    validate_steps(steps, templates_dir, known_routines, "", out)
    if not any(s.get("do") == "play_match" for s in _walk(steps)):
        out.append(("warn", "No play_match step: the routine never plays a match."))
    return out


def _walk(steps):
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, dict):
            yield s
            for nest in NESTED:
                if isinstance(s.get(nest), list):
                    yield from _walk(s[nest])


# ---------------------------------------------------------------- walking while recording unit placement
# Some players walk between placements (to reach a far corner, or to put a unit behind a wall). The recorder
# stores each burst of W/A/S/D as one "move" event with how long each key was held, and the replay holds the
# keys the same way (and lets go early when the character is stuck against a wall).
MOVE_KEYS = ("w", "a", "s", "d")


class MoveTracker:
    """Turns movement-key presses/releases (with times) into 'move' events.
    Keys held together (W+D = diagonal) belong to one event: {"type": "move", "keys": [{"key", "at", "hold"}, ...]}.
    `at` is when the key went down, relative to the start of the burst. Key auto-repeat (a held key sends the same
    press again and again) is ignored."""

    def __init__(self, keys=MOVE_KEYS):
        self.keys = tuple(keys)
        self.down = {}          # key -> press time
        self.burst = []         # [[key, press_time, release_time or None]]

    def is_move_key(self, char):
        return bool(char) and str(char).lower() in self.keys

    def press(self, char, t):
        k = str(char).lower()
        if k in self.down:
            return                       # auto-repeat of a key that is already held
        self.down[k] = t
        self.burst.append([k, t, None])

    def release(self, char, t):
        """Returns the finished event once every held key has been let go, else None."""
        k = str(char).lower()
        if k not in self.down:
            return None
        del self.down[k]
        for item in reversed(self.burst):
            if item[0] == k and item[2] is None:
                item[2] = t
                break
        if self.down:
            return None
        t0 = min(b[1] for b in self.burst)
        t1 = max(b[2] for b in self.burst)
        ev = {"type": "move", "start": t0, "end": t1,
              "keys": [{"key": b[0], "at": round(b[1] - t0, 3), "hold": round(max(0.0, b[2] - b[1]), 3)}
                       for b in self.burst]}
        self.burst = []
        return ev


def split_moves(events):
    """Cut a recording at its movement events: [("events", [...]), ("move", ev), ("events", [...])]."""
    out, cur = [], []
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "move":
            if cur:
                out.append(("events", cur))
                cur = []
            out.append(("move", ev))
        else:
            cur.append(ev)
    if cur:
        out.append(("events", cur))
    return out


def move_keys_of(ev):
    """[(key, at, hold)] of a move event (also accepts a plain {"key": "w", "hold": 1.2})."""
    keys = ev.get("keys")
    if not keys and ev.get("key"):
        keys = [{"key": ev["key"], "at": 0.0, "hold": ev.get("hold", 0.2)}]
    out = []
    for k in keys or []:
        try:
            out.append((str(k["key"]).lower(), max(0.0, float(k.get("at", 0.0))), max(0.0, float(k.get("hold", 0.0)))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


class WallDetector:
    """Decides that the character walked into a wall: the screen was scrolling (we saw movement) and then stopped
    changing for `stall` seconds while the key was still held. It is fed one number per look: how much the ground
    changed since the last look. Never fires if the screen was never seen moving (flat ground that looks the same
    all over), so a plain empty field can't be mistaken for a wall."""

    def __init__(self, stall=0.5, motion=3.0, still=1.2):
        self.stall, self.motion, self.still = stall, motion, still
        self.seen_motion = False
        self.still_since = None

    def update(self, diff, now):
        if diff >= self.motion:
            self.seen_motion = True
            self.still_since = None
        elif diff < self.still and self.seen_motion:
            if self.still_since is None:
                self.still_since = now
            elif now - self.still_since >= self.stall:
                return True
        return False


def compose_affine(*mats):
    """Chain 2x3 affine matrices (lists of lists). compose_affine(A, B, C) applies C first, then B, then A."""
    def to3(m):
        return [list(map(float, m[0])), list(map(float, m[1])), [0.0, 0.0, 1.0]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    out = to3(mats[0])
    for m in mats[1:]:
        out = mul(out, to3(m))
    return [out[0], out[1]]
