"""Extra windows and services for the BMO launcher: the TOOLS button.

Keep this file beside adventure_time_launcher.py and macro.py.

  History     every completed run / failure, with charts and CSV export
  Templates   score every picture against the live screen, and capture new ones from a screenshot
  Routines    build or edit adventures as data (routines/*.json) without touching Python
  Placements  see saved unit placements and re-record one
  Profiles    save / load groups of settings
  Remote      a tiny web page (token protected) to watch and control BMO from a phone

Everything Tk-related runs on the Tk thread. The web server runs on its own thread and only reads a
snapshot dict and puts commands on a queue, the same pattern the hotkey thread uses.
"""
import datetime
import hmac
import http.server
import json
import queue
import secrets
import socket
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import parse_qs, urlparse

import bmo_common as common
import bmo_theme as theme

HERE = Path(__file__).resolve().parent
MACRO = HERE / "macro.py"
TEMPLATES_DIR = HERE / "templates"
ROUTINES_DIR = HERE / "routines"
PLACEMENTS_DIR = HERE / "placements"
DEBUG_DIR = HERE / "debug"
PROFILES_DIR = HERE / "profiles"
HISTORY_FILE = HERE / "bmo_history.jsonl"
LIVE_SHOT = DEBUG_DIR / "live.jpg"
PROFILE_SKIP = {"window_x", "window_y", "corner", "ui_version", "discord_webhook", "dash_token",
                "dash_enabled", "adventure", "mode"}


# ============================================================ pure helpers (unit tested)
def parse_time(s):
    try:
        return datetime.datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def read_history(path=HISTORY_FILE):
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and parse_time(r.get("t")):
                out.append(r)
    except OSError:
        pass
    return out


def summarize(records, now=None):
    """Per-adventure totals, runs in each of the last 24 hours, and failures in the last 24 hours."""
    now = now or datetime.datetime.now()
    day_ago = now - datetime.timedelta(hours=24)
    start = now.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=23)
    per, hours, failures = {}, [0] * 24, 0
    for r in records:
        t = parse_time(r.get("t"))
        if t is None:
            continue
        kind = r.get("kind", "run")
        if kind == "failure":
            failures += t >= day_ago
            continue
        if kind != "run":
            continue
        s = per.setdefault(r.get("mode") or "?", {"runs": 0, "dur_total": 0.0, "dur_n": 0,
                                                   "best_wave": 0, "last24": 0})
        s["runs"] += 1
        d = r.get("dur")
        if isinstance(d, (int, float)) and d > 0:
            s["dur_total"] += d
            s["dur_n"] += 1
        w = r.get("wave")
        if isinstance(w, int) and w > s["best_wave"]:
            s["best_wave"] = w
        if t >= day_ago:
            s["last24"] += 1
        idx = int((t - start).total_seconds() // 3600)
        if 0 <= idx < 24:
            hours[idx] += 1
    return {"per": per, "hours": hours, "start": start, "failures24": failures}


def fmt_seconds(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def step_summary(step):
    """One short line describing a routine step for the editor's list."""
    bits = []
    for k, v in step.items():
        if k in ("do", "label", "optional", "retries"):
            continue
        if isinstance(v, list):
            bits.append(f"{k}=[{len(v)} step{'s' if len(v) != 1 else ''}]")
        else:
            bits.append(f"{k}={v}")
    extra = []
    if step.get("optional"):
        extra.append("optional")
    if step.get("retries"):
        extra.append(f"retries {step['retries']}")
    text = ", ".join(bits + extra)
    return text if len(text) < 72 else text[:69] + "..."


def routine_files():
    if not ROUTINES_DIR.is_dir():
        return []
    return [p for p in sorted(ROUTINES_DIR.glob("*.json")) if not p.stem.startswith("_")]


def template_names():
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.png")) if TEMPLATES_DIR.is_dir() else []


def local_addresses():
    """Best-effort list of this PC's LAN addresses (for the remote link)."""
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


# ============================================================ remote dashboard
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BMO remote</title>
<style>
body{font-family:system-ui,Arial,sans-serif;background:#55B8B0;color:#1d3d38;margin:0;padding:12px;max-width:560px;margin:auto}
.card{background:#DDF7E9;border-radius:14px;padding:12px;margin-bottom:10px;border:2px solid #287E80}
h1{margin:4px 0 8px;font-size:22px;color:#D94143}
#status{font-size:26px;font-weight:700}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
button,select{font-size:16px;padding:10px 14px;border-radius:10px;border:2px solid #fff;background:#286E91;color:#fff}
button.go{background:#327C4B}button.stop{background:#A63848}
img{width:100%;border-radius:10px;margin-top:6px;background:#222}
pre{white-space:pre-wrap;font-size:12px;margin:0}
small{color:#416B60}
</style></head><body>
<h1>BMO</h1>
<div class="card"><div id="status">...</div><div id="doing"></div><small id="meta"></small>
<div class="row"><button class="go" onclick="cmd('start')">Start</button>
<button onclick="cmd('pause')">Pause</button><button class="go" onclick="cmd('resume')">Resume</button>
<button class="stop" onclick="cmd('stop')">Stop</button></div>
<div class="row"><select id="adv" onchange="cmd('adventure',this.value)"></select></div></div>
<div class="card"><button onclick="cmd('snapshot')">Take a screenshot</button>
<label><input type="checkbox" id="live" checked> live (every 6s)</label><img id="shot" alt=""></div>
<div class="card"><pre id="log"></pre></div>
<script>
const T=new URLSearchParams(location.search).get('t')||'';
let lastShot=0,advFilled=false;
async function api(path,body){
  const r=await fetch(path,{method:body?'POST':'GET',headers:{'X-Token':T,'Content-Type':'application/json'},
    body:body?JSON.stringify(body):undefined});
  if(!r.ok)throw new Error(r.status);return r;}
async function cmd(c,v){try{await api('/api/cmd',{cmd:c,value:v});}catch(e){alert('Failed: '+e.message);}}
async function tick(){
  try{
    const s=await (await api('/api/status')).json();
    document.getElementById('status').textContent=s.status;
    document.getElementById('doing').textContent=(s.adventure||'')+(s.wave?' | wave '+s.wave:'')+(s.activity?' | '+s.activity:'');
    document.getElementById('meta').textContent=s.elapsed+' | '+s.runs+' runs | '+s.failures+' failed'+(s.plan?' | '+s.plan:'');
    document.getElementById('log').textContent=(s.log||[]).join('\\n');
    const sel=document.getElementById('adv');
    if(!advFilled||sel.options.length!==(s.adventures||[]).length){sel.innerHTML='';
      (s.adventures||[]).forEach(a=>{const o=document.createElement('option');o.value=a.key;o.textContent=a.label;sel.appendChild(o);});advFilled=true;}
    if(document.activeElement!==sel&&s.selected)sel.value=s.selected;
    if(s.shot_t&&s.shot_t!==lastShot){lastShot=s.shot_t;document.getElementById('shot').src='/api/shot?t='+encodeURIComponent(T)+'&_='+s.shot_t;}
  }catch(e){document.getElementById('status').textContent='offline ('+e.message+')';}
}
setInterval(tick,2000);tick();
setInterval(()=>{if(document.getElementById('live').checked)cmd('snapshot');},6000);
</script></body></html>"""

REMOTE_CMDS = ("start", "pause", "resume", "stop", "snapshot", "adventure")


def make_handler(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "BMO"

        def log_message(self, *args):
            pass

        def send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def authorized(self, query):
            token = str(app.settings.get("dash_token") or "")
            given = self.headers.get("X-Token") or (query.get("t") or [""])[0]
            ok = bool(token) and hmac.compare_digest(token.encode("utf-8"), str(given).encode("utf-8"))
            if not ok:
                time.sleep(0.4)              # slows down guessing
            return ok

        def host_ok(self):
            if app.settings.get("dash_lan"):
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in ("127.0.0.1", "localhost", "::1")   # blocks DNS-rebinding tricks in local-only mode

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self.host_ok():
                return self.send(403, '{"error":"bad host"}')
            if url.path in ("/", "/index.html"):
                return self.send(200, PAGE, "text/html; charset=utf-8")
            if not self.authorized(query):
                return self.send(401, '{"error":"bad token"}')
            if url.path == "/api/status":
                return self.send(200, json.dumps(app.dash_state))
            if url.path == "/api/shot":
                try:
                    return self.send(200, LIVE_SHOT.read_bytes(), "image/jpeg")
                except OSError:
                    return self.send(404, '{"error":"no screenshot yet"}')
            return self.send(404, '{"error":"not found"}')

        def do_POST(self):
            url = urlparse(self.path)
            if not self.host_ok():
                return self.send(403, '{"error":"bad host"}')
            # POST needs the token in a header (never the URL), which also blocks cross-site form posts
            if not self.headers.get("X-Token") or not self.authorized({}):
                return self.send(401, '{"error":"bad token"}')
            if url.path != "/api/cmd":
                return self.send(404, '{"error":"not found"}')
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= 2000:
                    raise ValueError
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                cmd = str(body.get("cmd"))
                value = body.get("value")
            except (ValueError, UnicodeDecodeError):
                return self.send(400, '{"error":"bad request"}')
            if cmd not in REMOTE_CMDS:
                return self.send(400, '{"error":"unknown command"}')
            if cmd == "adventure" and value not in [a["key"] for a in app.advs]:
                return self.send(400, '{"error":"unknown adventure"}')
            app.remote_cmds.put({"cmd": cmd, "value": value})
            return self.send(200, '{"ok":true}')

    return Handler


class DashServer:
    def __init__(self, app, host, port):
        self.app, self.host, self.port = app, host, port
        self.httpd = None

    def start(self):
        self.httpd = http.server.ThreadingHTTPServer((self.host, self.port), make_handler(self.app))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True).start()

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None


# ============================================================ the mixin
class ToolsMixin:
    """Mixed into BMO. Needs on self: root, settings, C, log, file_log, running, notes, python_exe, macro_env,
    send_control, set_setting, schedule_save, set_adventure, reload_adventures, advs, routines, check, entry,
    start, pause, resume, stop, win, clickthrough, set_clickthrough, open_path."""

    def tools_init(self):
        self.tools_window = None
        self.tool_vars = []
        self.tw = {}                       # widgets of the Tools window, by name
        self.oneshot_busy = set()
        self.preview = None
        self.capture = None
        self.step_dlg = None
        self.rt = {"path": None, "data": None, "dirty": False}
        self.dash = None
        self.dash_state = {}
        self.dash_last = 0.0
        self.dash_error = ""
        self.remote_cmds = queue.Queue()

    # ------------------------------------------------------------ small shared helpers
    def exclude_from_capture(self, w):
        """Keep BMO's own windows out of the macro's screenshots (they'd cover buttons it is looking for)."""
        if not (self.win.ok and self.settings.get("capture_exclusion", True)):
            return

        def go():
            try:
                self.win.capture_exclusion(self.win.toplevel(w), True)
            except Exception:
                pass
        try:
            w.after(250, go)
        except tk.TclError:
            pass

    def label(self, parent, text, bold=False, **kw):
        C = theme.palette(self.C)
        font = kw.pop("font", (theme.FONT, 9, "bold") if bold else (theme.FONT, 9))
        return tk.Label(parent, text=text, bg=C["screen"], fg=kw.pop("fg", C["ink"]), font=font, **kw)

    def button_(self, parent, text, cmd, color=None, **kw):
        C = self.C
        kind = {C["green"]: "green", C["amber"]: "amber", C["red"]: "red"}.get(color, "blue")
        return theme.Pill(parent, text, cmd, kind, height=28, size=8, bg=theme._bg(parent), **kw)

    def run_oneshot(self, tag, args, timeout=90):
        """Run macro.py with a one-shot flag in a worker thread; the events come back through self.notes."""
        if tag in self.oneshot_busy:
            return False
        if not MACRO.exists():
            self.log("macro.py not found beside the launcher.")
            return False
        self.oneshot_busy.add(tag)
        exe, env = self.python_exe(), self.macro_env()

        def work():
            events, tail = [], ""
            try:
                r = subprocess.run([str(exe), "-u", str(MACRO), *args], cwd=str(HERE), env=env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for line in r.stdout.splitlines():
                    if line.startswith(common.EVENT_PREFIX):
                        try:
                            events.append(json.loads(line[len(common.EVENT_PREFIX):]))
                        except ValueError:
                            pass
                    elif line.strip():
                        events.append({"event": "text", "text": line.strip()})
                if not any(e.get("event") != "text" for e in events):
                    tail = " ".join((r.stderr or "").strip().splitlines()[-2:]) or "macro.py gave no result"
            except subprocess.TimeoutExpired:
                tail = f"timed out after {timeout}s"
            except Exception as e:
                tail = str(e)[:100]
            self.notes.put(("oneshot", (tag, events, tail)))
        threading.Thread(target=work, daemon=True).start()
        return True

    def on_oneshot(self, tag, events, tail):
        self.oneshot_busy.discard(tag)
        try:
            if tag == "tpl":
                self.tpl_done(events, tail)
            elif tag == "grab":
                self.capture_open(events, tail)
            elif tag == "step":
                self.step_test_done(events, tail)
            elif tag == "snap":
                self.dash_last = 0.0         # refresh the dashboard state right away
        except tk.TclError:
            pass

    def set_text(self, widget_name, text, fg=None):
        w = self.tw.get(widget_name)
        if w is None:
            return
        try:
            w.configure(text=text, **({"fg": fg} if fg else {}))
        except tk.TclError:
            pass

    # ------------------------------------------------------------ Tools window
    def tools_menu(self, tab=0):
        if self.clickthrough:
            self.set_clickthrough(False)
        if self.tools_window is not None:
            try:
                if self.tools_window.winfo_exists():
                    self.tools_window.deiconify()
                    self.tools_window.lift()
                    self.tw["nb"].select(tab)
                    return
            except tk.TclError:
                pass
            self.tools_window = None
        C = theme.palette(self.C)
        w = tk.Toplevel(self.root)
        self.tools_window = w
        w.title("BMO's Tools")
        w.geometry(f"800x640+{self.pos[0] + 24}+{self.pos[1] + 24}")
        w.minsize(720, 560)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close_tools)
        self.exclude_from_capture(w)
        page = theme.frame_window(w, "BMO'S TOOLS")
        nb = ttk.Notebook(page)
        nb.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.tw["nb"] = nb
        for title, build in (("History", self.build_history_tab), ("Templates", self.build_templates_tab),
                             ("Routines", self.build_routines_tab), ("Placements", self.build_placements_tab),
                             ("Profiles", self.build_profiles_tab), ("Remote", self.build_remote_tab)):
            f = tk.Frame(nb, bg=C["screen"])
            nb.add(f, text=title)
            build(f)
        try:
            nb.select(tab)
        except tk.TclError:
            pass

    def close_tools(self):
        if self.rt.get("dirty"):
            if not messagebox.askyesno("Unsaved routine", "This routine has unsaved changes. Close anyway?",
                                       parent=self.tools_window):
                return
        self.close_step_dialog()
        self.close_capture()
        self.close_preview()
        w, self.tools_window = self.tools_window, None
        if w is not None:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self.tw.clear()
        self.tool_vars.clear()
        self.rt = {"path": None, "data": None, "dirty": False}
        import gc
        gc.collect()                       # free Tk variables here, on the Tk thread

    # ============================================================ History tab
    def history_append(self, rec):
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}
        try:
            with HISTORY_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        if self.tools_window is not None and rec.get("kind") in ("run", "failure"):
            self.later("hist", 400, self.refresh_history)

    def build_history_tab(self, f):
        C = theme.palette(self.C)
        self.tw["hist_summary"] = tk.Label(f, text="", justify="left", anchor="w", bg=C["screen"], fg=C["ink"],
                                           font=("Consolas", 9))
        self.tw["hist_summary"].pack(fill="x", padx=10, pady=(8, 0))
        self.tw["hist_canvas"] = tk.Canvas(f, height=120, bg=theme.T["paper_lt"], highlightthickness=1,
                                           highlightbackground=C["dark"])
        self.tw["hist_canvas"].pack(fill="x", padx=10, pady=6)
        box = tk.Frame(f, bg=C["screen"])
        box.pack(fill="both", expand=True, padx=10)
        cols = (("time", "Time", 150), ("what", "Adventure", 170), ("dur", "Took", 80),
                ("wave", "Wave", 60), ("map", "Map / note", 200))
        tree = ttk.Treeview(box, columns=[c[0] for c in cols], show="headings", height=9)
        for key, text, width in cols:
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        bar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.tw["hist_tree"] = tree
        row = tk.Frame(f, bg=C["screen"])
        row.pack(fill="x", padx=10, pady=8)
        self.button_(row, "Refresh", self.refresh_history).pack(side="left")
        self.button_(row, "Export CSV", self.export_history).pack(side="left", padx=6)
        self.button_(row, "Open file", lambda: self.open_path(HISTORY_FILE, "bmo_history.jsonl")
                     if HISTORY_FILE.exists() else self.log("No history yet.")).pack(side="left")
        self.button_(row, "Clear history", self.clear_history, C["amber"]).pack(side="right")
        self.refresh_history()

    def refresh_history(self):
        tree, canvas = self.tw.get("hist_tree"), self.tw.get("hist_canvas")
        if tree is None or canvas is None:
            return
        records = read_history()
        s = summarize(records)
        lines = []
        total24 = sum(v["last24"] for v in s["per"].values())
        lines.append(f"Last 24 h: {total24} runs, {s['failures24']} failed attempts.     All time: "
                     f"{sum(v['runs'] for v in s['per'].values())} runs logged.")
        label = {a["key"]: a["label"].title() for a in self.advs}
        for key, v in sorted(s["per"].items(), key=lambda kv: -kv[1]["runs"]):
            avg = fmt_seconds(v["dur_total"] / v["dur_n"]) if v["dur_n"] else "-"
            best = f"  best wave {v['best_wave']}" if v["best_wave"] else ""
            lines.append(f"  {label.get(key, key)[:24]:<24} {v['runs']:>5} runs   avg {avg:>7}{best}   (24h: {v['last24']})")
        if not s["per"]:
            lines.append("  No runs logged yet - they appear here as the macro completes them.")
        self.set_text("hist_summary", "\n".join(lines[:7]))
        # bar chart: runs per hour, last 24 hours
        canvas.delete("all")
        try:
            canvas.update_idletasks()
            width = max(300, canvas.winfo_width())
        except tk.TclError:
            width = 700
        height, left, bottom = 120, 30, 16
        peak = max(max(s["hours"]), 1)
        bw = (width - left - 10) / 24
        canvas.create_text(4, 8, text=str(peak), anchor="w", font=("Consolas", 7), fill="#416B60")
        canvas.create_text(4, height - bottom, text="0", anchor="w", font=("Consolas", 7), fill="#416B60")
        for i, n in enumerate(s["hours"]):
            x1 = left + i * bw + 1
            y1 = height - bottom - (height - bottom - 14) * n / peak
            if n:
                canvas.create_rectangle(x1, y1, x1 + bw - 2, height - bottom, fill=self.C["green"], outline="")
            if i % 6 == 0:
                hour = (s["start"] + datetime.timedelta(hours=i)).strftime("%H")
                canvas.create_text(x1, height - 6, text=hour, anchor="w", font=("Consolas", 7), fill="#416B60")
        canvas.create_text(width - 8, 8, text="runs per hour, last 24 h", anchor="e", font=("Consolas", 7),
                           fill="#416B60")
        # list
        tree.delete(*tree.get_children())
        shown = [r for r in records if r.get("kind", "run") in ("run", "failure")][-300:]
        for r in reversed(shown):
            t = parse_time(r.get("t"))
            when = t.strftime("%m-%d %H:%M:%S") if t else "?"
            if r.get("kind") == "failure":
                tree.insert("", "end", values=(when, label.get(r.get("mode"), r.get("mode") or ""), "", "",
                                               f"failed attempt #{r.get('streak', '?')}"))
            else:
                d = r.get("dur")
                tree.insert("", "end", values=(when, label.get(r.get("mode"), r.get("mode") or "?"),
                                               fmt_seconds(d) if isinstance(d, (int, float)) else "",
                                               r.get("wave") or "", r.get("map") or ""))

    def export_history(self):
        out = HERE / "bmo_history.csv"
        try:
            import csv
            with out.open("w", newline="", encoding="utf-8") as f:
                wr = csv.writer(f)
                wr.writerow(["time", "kind", "adventure", "duration_s", "wave", "map", "streak"])
                for r in read_history():
                    wr.writerow([r.get("t"), r.get("kind", "run"), r.get("mode", ""), r.get("dur", ""),
                                 r.get("wave", ""), r.get("map", ""), r.get("streak", "")])
            self.log("Exported bmo_history.csv")
            self.open_path(out, "bmo_history.csv")
        except Exception as e:
            self.log(f"Export failed: {e}")

    def clear_history(self):
        if not messagebox.askyesno("Clear history", "Delete the whole run history? This can't be undone.",
                                   parent=self.tools_window):
            return
        try:
            HISTORY_FILE.unlink()
        except OSError:
            pass
        self.refresh_history()

    # ============================================================ Templates tab
    def build_templates_tab(self, f):
        C = theme.palette(self.C)
        self.label(f, "Scores every picture the macro looks for against what is on your screen RIGHT NOW. "
                      "Open Roblox on the screen you want to test (BMO's windows are hidden from the capture). "
                      "Green = the macro would find it.", wraplength=740, justify="left").pack(
            anchor="w", padx=10, pady=(8, 4))
        row = tk.Frame(f, bg=C["screen"])
        row.pack(fill="x", padx=10)
        self.button_(row, "Test selected (shows a picture)", self.tpl_test_selected).pack(side="left")
        self.button_(row, "Test all", self.tpl_test_all).pack(side="left", padx=6)
        self.button_(row, "Capture new picture...", self.capture_start, C["green"]).pack(side="left")
        self.button_(row, "Refresh list", self.fill_templates).pack(side="right")
        box = tk.Frame(f, bg=C["screen"])
        box.pack(fill="both", expand=True, padx=10, pady=6)
        tree = ttk.Treeview(box, columns=("name", "score", "need", "state"), show="headings", height=14,
                            selectmode="browse")
        for key, text, width, anchor in (("name", "Picture", 330, "w"), ("score", "Score", 80, "e"),
                                         ("need", "Macro needs", 100, "e"), ("state", "", 200, "w")):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor=anchor)
        bar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.tw["tpl_tree"] = tree
        self.tw["tpl_status"] = self.label(f, "", wraplength=740, justify="left")
        self.tw["tpl_status"].pack(anchor="w", padx=10, pady=(0, 8))
        self.fill_templates()

    def fill_templates(self):
        tree = self.tw.get("tpl_tree")
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for name in template_names():
            tree.insert("", "end", iid=name, values=(name, "", "", ""))

    def tpl_test_selected(self):
        tree = self.tw.get("tpl_tree")
        sel = tree.selection() if tree is not None else ()
        if not sel:
            self.set_text("tpl_status", "Click a picture in the list first.")
            return
        self.tpl_run([sel[0]])

    def tpl_test_all(self):
        self.tpl_run([])

    def tpl_run(self, names):
        if self.run_oneshot("tpl", ["--test-templates", *names], timeout=240):
            self.set_text("tpl_status", "Testing against the screen... (testing all can take 20-40 seconds)")
        else:
            self.set_text("tpl_status", "A test is already running.")

    def tpl_done(self, events, tail):
        tree = self.tw.get("tpl_tree")
        found = missing = 0
        image = None
        for e in events:
            kind = e.get("event")
            if kind == "tpl" and tree is not None:
                name = e.get("name")
                if not tree.exists(name):
                    tree.insert("", "end", iid=name, values=(name, "", "", ""))
                if e.get("missing"):
                    tree.item(name, values=(name, "-", "", "\u2718 file missing"))
                    missing += 1
                    continue
                ok = bool(e.get("found"))
                found += ok
                score, need = float(e.get("score", 0)), float(e.get("need", 0))
                near = "" if ok else ("  (close)" if score >= need - 0.1 else "")
                tree.item(name, values=(name, f"{score:.2f}", f"{need:.2f}",
                                        ("\u2714 found" if ok else "\u2718 not seen") + near),
                          tags=("ok" if ok else "bad",))
            elif kind == "tpl_done":
                image = e.get("image")
        if tree is not None:
            tree.tag_configure("ok", foreground=self.C["green"])
            tree.tag_configure("bad", foreground=self.C["red"])
        if not any(e.get("event") == "tpl" for e in events):
            self.set_text("tpl_status", "No result: " + (tail or "is macro.py the newest version?"), self.C["red"])
            return
        n = sum(1 for e in events if e.get("event") == "tpl")
        self.set_text("tpl_status", f"{found} of {n} pictures visible right now"
                                    + (f", {missing} files missing" if missing else "")
                                    + ". Pictures that aren't on screen right now scoring low is normal.")
        if image and Path(image).exists():
            self.show_preview(image, "Template test")

    # ---- preview window (a picture in its own small window)
    def show_preview(self, path, title):
        C = theme.palette(self.C)
        try:
            img = tk.PhotoImage(file=str(path))
        except tk.TclError as e:
            self.log(f"Couldn't show the picture: {e}")
            return
        if self.preview is None or not self._alive(self.preview["win"]):
            w = tk.Toplevel(self.root)
            w.configure(bg=C["screen"])
            w.attributes("-topmost", True)
            w.protocol("WM_DELETE_WINDOW", self.close_preview)
            self.exclude_from_capture(w)
            lbl = tk.Label(w, bg=C["screen"])
            lbl.pack(padx=6, pady=6)
            self.preview = {"win": w, "label": lbl, "img": None}
        p = self.preview
        p["win"].title(title)
        p["img"] = img                      # keep a reference or Tk shows nothing
        p["label"].configure(image=img)
        p["win"].deiconify()
        p["win"].lift()

    def close_preview(self):
        p, self.preview = self.preview, None
        if p is not None:
            try:
                p["win"].destroy()
            except tk.TclError:
                pass

    @staticmethod
    def _alive(w):
        try:
            return bool(w.winfo_exists())
        except tk.TclError:
            return False

    # ---- capture a new picture
    def capture_start(self):
        if self.run_oneshot("grab", ["--grab"], timeout=60):
            self.set_text("tpl_status", "Grabbing the Roblox window...")

    def capture_open(self, events, tail):
        ev = next((e for e in events if e.get("event") == "grab"), None)
        if not ev or not ev.get("path") or not Path(ev["path"]).exists():
            self.set_text("tpl_status", "Couldn't grab the screen: " + (tail or "unknown error"), self.C["red"])
            return
        self.set_text("tpl_status", "")
        self.close_capture()
        self.capture_res = str(ev.get("res") or "")     # "" = ~1280x720; else the templates/<res> folder to save into
        C = theme.palette(self.C)
        try:
            img = tk.PhotoImage(file=ev["path"])
        except tk.TclError as e:
            self.set_text("tpl_status", f"Couldn't load the grab: {e}", C["red"])
            return
        w = tk.Toplevel(self.root)
        w.title("Capture a picture - drag a box around it")
        w.configure(bg=C["screen"])
        w.attributes("-topmost", True)
        w.geometry(f"{min(img.width() + 40, 1100)}x{min(img.height() + 120, 780)}+40+40")
        w.protocol("WM_DELETE_WINDOW", self.close_capture)
        self.exclude_from_capture(w)
        note = ("Drag a box tightly around the button / icon / text the macro should look for. "
                "Keep Roblox at 1280x720 so the picture matches the macro's." +
                ("" if ev.get("window") else "  (Roblox window not found: this is the whole screen.)"))
        self.label(w, note, wraplength=1000, justify="left").pack(anchor="w", padx=8, pady=(6, 2))
        frame = tk.Frame(w, bg=C["screen"])
        frame.pack(fill="both", expand=True, padx=8)
        canvas = tk.Canvas(frame, bg="#222222", highlightthickness=0, cursor="crosshair",
                           scrollregion=(0, 0, img.width(), img.height()))
        xs = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        ys = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.create_image(0, 0, image=img, anchor="nw")
        state = {"win": w, "img": img, "box": None, "rect": None, "x0": 0, "y0": 0}
        self.capture = state
        name_var = tk.StringVar(master=w)
        info_var = tk.StringVar(master=w, value="No box yet.")
        state["vars"] = (name_var, info_var)

        def press(e):
            state["x0"], state["y0"] = int(canvas.canvasx(e.x)), int(canvas.canvasy(e.y))
            if state["rect"] is not None:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(state["x0"], state["y0"], state["x0"], state["y0"],
                                                    outline="#ff3355", width=2)

        def drag(e):
            x1, y1 = int(canvas.canvasx(e.x)), int(canvas.canvasy(e.y))
            canvas.coords(state["rect"], state["x0"], state["y0"], x1, y1)
            left, right = sorted((state["x0"], x1))
            top, bottom = sorted((state["y0"], y1))
            left, top = max(0, left), max(0, top)
            right, bottom = min(img.width(), right), min(img.height(), bottom)
            state["box"] = (left, top, right, bottom)
            info_var.set(f"Box: {right - left} x {bottom - top} px")

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        row = tk.Frame(w, bg=C["screen"])
        row.pack(fill="x", padx=8, pady=8)
        self.label(row, "Save as (no .png):", bold=True).pack(side="left")
        tk.Entry(row, textvariable=name_var, width=28).pack(side="left", padx=6)
        self.button_(row, "Save picture", self.capture_save, C["green"]).pack(side="left")
        tk.Label(row, textvariable=info_var, bg=C["screen"], fg=C["ink"], font=("Arial", 8)).pack(side="left", padx=10)

    def capture_save(self):
        st = self.capture
        if not st or not st.get("box"):
            return
        name_var, info_var = st["vars"]
        name = common.slug(name_var.get())
        left, top, right, bottom = st["box"]
        if not name:
            info_var.set("Type a name first (letters, numbers, underscores).")
            return
        if right - left < 6 or bottom - top < 6:
            info_var.set("The box is too small - drag a bigger one.")
            return
        res = getattr(self, "capture_res", "") or ""
        out_dir = TEMPLATES_DIR / res if res else TEMPLATES_DIR
        shown = f"templates/{res}/{name}.png" if res else f"templates/{name}.png"
        dest = out_dir / f"{name}.png"
        if dest.exists() and not messagebox.askyesno("Replace?", f"{shown} already exists. Replace it?",
                                                     parent=st["win"]):
            return
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            # Tk can write a cropped part of an image straight to a PNG (end coordinates are exclusive)
            self.root.tk.call(str(st["img"]), "write", str(dest), "-format", "png",
                              "-from", left, top, right, bottom)
        except tk.TclError as e:
            info_var.set(f"Save failed: {e}")
            return
        self.log(f"Saved {shown} ({right - left}x{bottom - top}).")
        info_var.set(f"Saved {shown}")
        self.fill_templates()

    def close_capture(self):
        st, self.capture = self.capture, None
        if st is not None:
            try:
                st["win"].destroy()
            except tk.TclError:
                pass

    # ============================================================ Routines tab (the editor)
    def build_routines_tab(self, f):
        C = theme.palette(self.C)
        left = tk.Frame(f, bg=C["screen"])
        left.pack(side="left", fill="y", padx=(10, 4), pady=8)
        self.label(left, "ROUTINES", bold=True).pack(anchor="w")
        lb = tk.Listbox(left, width=24, height=16, exportselection=False)
        lb.pack(fill="y", expand=True)
        lb.bind("<<ListboxSelect>>", lambda e: self.rt_pick())
        self.tw["rt_list"] = lb
        for text, cmd in (("New routine", self.rt_new), ("Duplicate", self.rt_duplicate),
                          ("Delete", self.rt_delete), ("Reload files", self.rt_fill_list)):
            self.button_(left, text, cmd).pack(fill="x", pady=2)
        right = tk.Frame(f, bg=C["screen"])
        right.pack(side="left", fill="both", expand=True, padx=(4, 10), pady=8)
        meta = tk.Frame(right, bg=C["screen"])
        meta.pack(fill="x")
        self.rt_vars = {}
        for i, (key, text, width) in enumerate((("name", "Name", 22), ("label", "Button text", 22),
                                                ("short", "Stats name", 12), ("map", "Map (placements)", 14))):
            self.label(meta, text, bold=True).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 4), pady=2)
            v = tk.StringVar(master=meta)
            self.tool_vars.append(v)
            v.trace_add("write", lambda *_: self.rt_dirty())
            tk.Entry(meta, textvariable=v, width=width).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w",
                                                             padx=(0, 12), pady=2)
            self.rt_vars[key] = v
        self.label(meta, "Match kind", bold=True).grid(row=2, column=0, sticky="w", pady=2)
        kv = tk.StringVar(master=meta)
        self.tool_vars.append(kv)
        kv.trace_add("write", lambda *_: self.rt_dirty())
        ttk.Combobox(meta, textvariable=kv, values=["", *common.MATCH_KINDS], state="readonly",
                     width=12).grid(row=2, column=1, sticky="w")
        self.rt_vars["kind"] = kv
        tree = ttk.Treeview(right, columns=("n", "do", "detail"), show="headings", height=11, selectmode="browse")
        for key, text, width in (("n", "#", 30), ("do", "Step", 110), ("detail", "Details", 380)):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True, pady=(6, 4))
        tree.bind("<Double-1>", lambda e: self.rt_edit_step())
        self.tw["rt_tree"] = tree
        row = tk.Frame(right, bg=C["screen"])
        row.pack(fill="x")
        for text, cmd in (("Add step", self.rt_add_step), ("Edit", self.rt_edit_step), ("Up", lambda: self.rt_move(-1)),
                          ("Down", lambda: self.rt_move(1)), ("Delete step", self.rt_del_step)):
            self.button_(row, text, cmd).pack(side="left", padx=(0, 4))
        row = tk.Frame(right, bg=C["screen"])
        row.pack(fill="x", pady=4)
        self.button_(row, "Save", self.rt_save, C["green"]).pack(side="left", padx=(0, 4))
        self.button_(row, "Validate", self.rt_validate).pack(side="left", padx=(0, 4))
        self.button_(row, "Test selected step", self.rt_test_step).pack(side="left", padx=(0, 4))
        self.button_(row, "Use as my adventure", self.rt_use, C["amber"]).pack(side="left")
        msgs = tk.Text(right, height=5, wrap="word", bg=theme.T["paper_lt"], fg=C["ink"], font=("Consolas", 8), relief="flat")
        msgs.pack(fill="x", pady=(4, 0))
        msgs.tag_configure("ok", foreground=C["green"])
        msgs.tag_configure("warn", foreground=C["amber"])
        msgs.tag_configure("bad", foreground=C["red"])
        msgs.configure(state="disabled")
        self.tw["rt_msgs"] = msgs
        self.rt_fill_list()

    def rt_msg(self, lines):
        t = self.tw.get("rt_msgs")
        if t is None:
            return
        t.configure(state="normal")
        t.delete("1.0", "end")
        for lvl, text in lines:
            t.insert("end", {"ok": "\u2714 ", "warn": "! ", "bad": "\u2718 "}.get(lvl, "") + text + "\n", lvl)
        t.configure(state="disabled")

    def rt_dirty(self):
        if self.rt.get("loading") or self.rt.get("data") is None:
            return
        self.rt["dirty"] = True

    def rt_fill_list(self):
        lb = self.tw.get("rt_list")
        if lb is None:
            return
        lb.delete(0, "end")
        self.rt_files = routine_files()
        for p in self.rt_files:
            lb.insert("end", p.stem)
        if self.rt.get("path") in self.rt_files:
            lb.selection_set(self.rt_files.index(self.rt["path"]))

    def rt_confirm_discard(self):
        if self.rt.get("dirty"):
            return messagebox.askyesno("Unsaved changes", "Discard the unsaved changes to this routine?",
                                       parent=self.tools_window)
        return True

    def rt_pick(self):
        lb = self.tw.get("rt_list")
        sel = lb.curselection() if lb is not None else ()
        if not sel:
            return
        path = self.rt_files[sel[0]]
        if path == self.rt.get("path"):
            return
        if not self.rt_confirm_discard():
            if self.rt.get("path") in self.rt_files:
                lb.selection_clear(0, "end")
                lb.selection_set(self.rt_files.index(self.rt["path"]))
            return
        self.rt_load(path)

    def rt_load(self, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a routine")
            data.setdefault("steps", [])
        except Exception as e:
            self.rt_msg([("bad", f"Couldn't read {Path(path).name}: {e}")])
            return
        self.rt = {"path": Path(path), "data": data, "dirty": False, "loading": True}
        for key, var in self.rt_vars.items():
            var.set(str(data.get(key) or ""))
        self.rt["loading"] = False
        self.rt_refresh()
        self.rt_validate()

    def rt_refresh(self, select=None):
        tree = self.tw.get("rt_tree")
        data = self.rt.get("data")
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for i, step in enumerate((data or {}).get("steps", [])):
            if isinstance(step, dict):
                lab = step.get("label")
                tree.insert("", "end", iid=str(i), values=(i + 1, step.get("do", "?"),
                                                           (f"[{lab}] " if lab else "") + step_summary(step)))
            else:
                tree.insert("", "end", iid=str(i), values=(i + 1, "?", "not a valid step"))
        if select is not None and tree.exists(str(select)):
            tree.selection_set(str(select))
            tree.see(str(select))

    def rt_selected_index(self):
        tree = self.tw.get("rt_tree")
        sel = tree.selection() if tree is not None else ()
        return int(sel[0]) if sel else None

    def rt_sync_meta(self):
        data = self.rt.get("data")
        if data is None:
            return
        for key, var in self.rt_vars.items():
            v = var.get().strip()
            if v:
                data[key] = v
            else:
                data.pop(key, None)

    def rt_new(self):
        if not self.rt_confirm_discard():
            return
        name = simpledialog.askstring("New routine", "Name for the new routine:", parent=self.tools_window)
        if not name or not name.strip():
            return
        path = self.rt_unique_path(common.slug(name) or "routine")
        data = {"name": name.strip(), "kind": "challenge", "steps": [{"do": "play_match"}]}
        self.rt_write(path, data)
        self.rt_fill_list()
        self.reload_adventures()
        self.rt_load(path)
        self.rt_fill_list()

    def rt_unique_path(self, stem):
        ROUTINES_DIR.mkdir(exist_ok=True)
        p, i = ROUTINES_DIR / f"{stem}.json", 2
        while p.exists():
            p = ROUTINES_DIR / f"{stem}_{i}.json"
            i += 1
        return p

    @staticmethod
    def rt_write(path, data):
        common.write_json_atomic(path, data, indent=2, ensure_ascii=False)

    def rt_duplicate(self):
        if self.rt.get("data") is None:
            return
        self.rt_sync_meta()
        data = json.loads(json.dumps(self.rt["data"]))
        data["name"] = str(data.get("name") or self.rt["path"].stem) + " copy"
        data.pop("label", None)
        path = self.rt_unique_path(common.slug(data["name"]))
        self.rt_write(path, data)
        self.reload_adventures()
        self.rt_fill_list()
        self.rt_load(path)
        self.rt_fill_list()

    def rt_delete(self):
        path = self.rt.get("path")
        if path is None:
            return
        if not messagebox.askyesno("Delete routine", f"Delete {path.name}? (It is moved to a _trash file.)",
                                   parent=self.tools_window):
            return
        try:
            path.replace(path.with_name(f"_trash_{time.strftime('%Y%m%d_%H%M%S')}_{path.name}"))
        except OSError as e:
            self.rt_msg([("bad", f"Couldn't delete: {e}")])
            return
        self.rt = {"path": None, "data": None, "dirty": False}
        for var in self.rt_vars.values():
            var.set("")
        self.rt_refresh()
        self.reload_adventures()
        self.rt_fill_list()

    def rt_save(self):
        data, path = self.rt.get("data"), self.rt.get("path")
        if data is None or path is None:
            return
        self.rt_sync_meta()
        if not data.get("name"):
            data["name"] = path.stem
        try:
            self.rt_write(path, data)
        except OSError as e:
            self.rt_msg([("bad", f"Couldn't save: {e}")])
            return
        self.rt["dirty"] = False
        self.reload_adventures()
        self.rt_validate(saved=True)

    def rt_validate(self, saved=False):
        data = self.rt.get("data")
        if data is None:
            return
        self.rt_sync_meta()
        probs = common.validate_routine(data, TEMPLATES_DIR, tuple(self.routines))
        lines = [("ok", "Saved.")] if saved else []
        if not any(lvl == "bad" for lvl, _ in probs):
            lines.append(("ok", f"{len(data.get('steps', []))} steps, every picture it needs exists."))
        self.rt_msg(lines + probs)

    def rt_add_step(self):
        if self.rt.get("data") is None:
            self.rt_msg([("warn", "Pick or create a routine first.")])
            return
        sel = self.rt_selected_index()

        def done(step):
            steps = self.rt["data"].setdefault("steps", [])
            at = len(steps) if sel is None else sel + 1
            steps.insert(at, step)
            self.rt["dirty"] = True
            self.rt_refresh(at)
        self.step_dialog({"do": "click_template"}, done)

    def rt_edit_step(self):
        i = self.rt_selected_index()
        data = self.rt.get("data")
        if data is None or i is None:
            return
        step = data["steps"][i]
        if not isinstance(step, dict):
            return

        def done(new):
            data["steps"][i] = new
            self.rt["dirty"] = True
            self.rt_refresh(i)
        self.step_dialog(dict(step), done)

    def rt_move(self, delta):
        i, data = self.rt_selected_index(), self.rt.get("data")
        if data is None or i is None:
            return
        j = i + delta
        if 0 <= j < len(data["steps"]):
            data["steps"][i], data["steps"][j] = data["steps"][j], data["steps"][i]
            self.rt["dirty"] = True
            self.rt_refresh(j)

    def rt_del_step(self):
        i, data = self.rt_selected_index(), self.rt.get("data")
        if data is None or i is None:
            return
        del data["steps"][i]
        self.rt["dirty"] = True
        self.rt_refresh(min(i, len(data["steps"]) - 1) if data["steps"] else None)

    def rt_test_step(self):
        i, data = self.rt_selected_index(), self.rt.get("data")
        if data is None or i is None:
            self.rt_msg([("warn", "Select a step to test first.")])
            return
        if self.running:
            self.rt_msg([("warn", "Stop the macro first - a test would fight it for the mouse.")])
            return
        step = data["steps"][i]
        if self.run_oneshot("step", ["--step", json.dumps(step)], timeout=240):
            self.rt_msg([("warn", f"Testing step {i + 1} ({step.get('do')}): BMO is bringing Roblox to the front "
                                  "and running it. Hands off the mouse. Mouse to the top-left corner aborts.")])
        else:
            self.rt_msg([("warn", "A step test is already running.")])

    def step_test_done(self, events, tail):
        ok = next((e.get("ok") for e in events if e.get("event") == "step_done"), None)
        texts = [e.get("text", "") for e in events if e.get("event") == "text"][-4:]
        if ok is None:
            self.rt_msg([("bad", "The test didn't finish: " + (tail or "no result"))] + [("warn", t) for t in texts])
        else:
            self.rt_msg([("ok" if ok else "bad", "That step worked." if ok else "That step did NOT work.")]
                        + [("warn", t) for t in texts])

    def rt_use(self):
        path = self.rt.get("path")
        if path is None:
            return
        if self.rt.get("dirty"):
            self.rt_save()
        for key, r in self.routines.items():
            if Path(r["file"]) == path:
                self.set_adventure(key)
                self.rt_msg([("ok", f"'{r['label']}' is now BMO's selected adventure.")])
                return
        self.rt_msg([("bad", "BMO can't see that routine yet (is the JSON valid?).")])

    # ---- the step dialog
    def close_step_dialog(self):
        d, self.step_dlg = self.step_dlg, None
        if d is not None:
            try:
                d["win"].destroy()
            except tk.TclError:
                pass

    def step_dialog(self, initial, on_ok):
        C = theme.palette(self.C)
        self.close_step_dialog()
        w = tk.Toplevel(self.tools_window or self.root)
        w.title("Routine step")
        w.geometry(f"520x700+{self.pos[0] + 60}+{self.pos[1] + 60}")
        w.configure(bg=C["screen"])
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close_step_dialog)
        self.exclude_from_capture(w)
        d = {"win": w, "vars": {}, "texts": {}, "values": dict(initial)}
        self.step_dlg = d
        action_var = tk.StringVar(master=w, value=initial.get("do") or "click_template")
        d["action"] = action_var
        theme.Banner(w, "ROUTINE STEP", bg=C["screen"], height=52, size=15).pack(fill="x", padx=6, pady=(6, 0))
        top = tk.Frame(w, bg=C["screen"])
        top.pack(fill="x", padx=10, pady=(10, 2))
        self.label(top, "What to do:", bold=True).pack(side="left")
        combo = ttk.Combobox(top, textvariable=action_var, values=list(common.STEP_SCHEMA), state="readonly", width=22)
        combo.pack(side="left", padx=6)
        doc = self.label(w, "", wraplength=470, justify="left", fg=C["blue"])
        doc.pack(anchor="w", padx=10, pady=(2, 4))
        form = tk.Frame(w, bg=C["screen"])
        form.pack(fill="both", expand=True, padx=10)
        err = self.label(w, "", wraplength=470, justify="left", fg=C["red"])
        err.pack(anchor="w", padx=10)
        names = template_names()

        def build(*_):
            for child in form.winfo_children():
                child.destroy()
            d["vars"], d["texts"] = {}, {}
            spec = common.STEP_SCHEMA[action_var.get()]
            d["built"] = action_var.get()          # the form on screen belongs to this action
            doc.configure(text=spec["doc"])
            vals = d["values"]
            for fld in spec["fields"] + common.COMMON_FIELDS:
                name, typ = fld["name"], fld["type"]
                row = tk.Frame(form, bg=C["screen"])
                row.pack(fill="x", pady=2)
                star = " *" if fld["required"] else ""
                self.label(row, name + star, bold=True, width=12, anchor="w").pack(side="left", anchor="n")
                val = vals.get(name, fld["default"])
                if typ == "bool":
                    var = tk.BooleanVar(master=w, value=bool(val))
                    tk.Checkbutton(row, variable=var, bg=C["screen"]).pack(side="left")
                elif typ == "steps":
                    txt = tk.Text(row, height=4, width=40, font=("Consolas", 8))
                    txt.insert("1.0", json.dumps(val, indent=1) if val else "[]")
                    txt.pack(side="left")
                    d["texts"][name] = txt
                    var = None
                else:
                    var = tk.StringVar(master=w, value="" if val is None else str(val))
                    if typ == "template":
                        ttk.Combobox(row, textvariable=var, values=names, width=30).pack(side="left")
                    elif typ == "choice":
                        ttk.Combobox(row, textvariable=var, values=list(fld["choices"] or []), state="readonly",
                                     width=14).pack(side="left")
                    else:
                        tk.Entry(row, textvariable=var, width=34).pack(side="left")
                if var is not None:
                    d["vars"][name] = var
                if fld["help"]:
                    self.label(form, "      " + fld["help"], wraplength=450, justify="left", fg="#416B60",
                               font=("Arial", 7)).pack(anchor="w")

        def collect():
            act = d["built"]
            spec = common.STEP_SCHEMA[act]
            out = {"do": act}
            for fld in spec["fields"] + common.COMMON_FIELDS:
                name, typ = fld["name"], fld["type"]
                if typ == "steps":
                    raw = d["texts"][name].get("1.0", "end").strip()
                    if raw and raw != "[]":
                        try:
                            val = json.loads(raw)
                            if not isinstance(val, list):
                                raise ValueError
                        except ValueError:
                            raise ValueError(f"'{name}' must be a JSON list of steps, like "
                                             '[{"do": "press", "key": "e"}]')
                        out[name] = val
                    continue
                v = d["vars"][name].get()
                if typ == "bool":
                    if bool(v) != bool(fld["default"]):
                        out[name] = bool(v)
                    continue
                v = str(v).strip()
                if v == "":
                    if fld["required"]:
                        raise ValueError(f"'{name}' is required")
                    continue
                if typ in ("int", "float"):
                    try:
                        num = int(float(v)) if typ == "int" else float(v)
                    except ValueError:
                        raise ValueError(f"'{name}' must be a number")
                    if num != (fld["default"] or 0) or fld["required"]:
                        out[name] = num
                elif typ == "choice":
                    if v != (fld["default"] or "") or fld["required"]:
                        out[name] = v
                else:
                    out[name] = v
            return out

        def shared():
            """The fields every step has (label / optional / retries), read from the form on screen."""
            v, out = d["vars"], {}
            label = str(v["label"].get()).strip()
            if label:
                out["label"] = label
            if v["optional"].get():
                out["optional"] = True
            try:
                retries = int(float(str(v["retries"].get()).strip() or 0))
            except ValueError:
                retries = 0
            if retries:
                out["retries"] = retries
            return out

        def changed(*_):
            d["values"] = shared()                 # carry them over to the new action's form
            build()
        combo.bind("<<ComboboxSelected>>", changed)

        def ok():
            try:
                step = collect()
            except ValueError as e:
                err.configure(text=str(e))
                return
            on_ok(step)
            self.close_step_dialog()
        row = tk.Frame(w, bg=C["screen"])
        row.pack(fill="x", padx=10, pady=10)
        self.button_(row, "OK", ok, C["green"]).pack(side="left")
        self.button_(row, "Cancel", self.close_step_dialog, C["amber"]).pack(side="right")
        build()

    # ============================================================ Placements tab
    def build_placements_tab(self, f):
        C = theme.palette(self.C)
        self.label(f, "Unit placements the macro recorded for each map (placements/<map>.json). "
                      "'Re-record' moves the file aside; the next run on that map asks you to place your units "
                      "again (press F8 when done).", wraplength=740, justify="left").pack(anchor="w", padx=10, pady=8)
        box = tk.Frame(f, bg=C["screen"])
        box.pack(fill="both", expand=True, padx=10)
        tree = ttk.Treeview(box, columns=("map", "actions", "when"), show="headings", height=14, selectmode="browse")
        for key, text, width in (("map", "Map", 260), ("actions", "Actions", 100), ("when", "Recorded", 200)):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        self.tw["pl_tree"] = tree
        row = tk.Frame(f, bg=C["screen"])
        row.pack(fill="x", padx=10, pady=8)
        self.button_(row, "Re-record selected", self.pl_rerecord, C["amber"]).pack(side="left")
        self.button_(row, "Open folder", lambda: self.open_path(PLACEMENTS_DIR, "placements")
                     if PLACEMENTS_DIR.exists() else self.log("No placements folder yet.")).pack(side="left", padx=6)
        self.button_(row, "Refresh", self.pl_fill).pack(side="right")
        self.tw["pl_status"] = self.label(f, "", wraplength=740, justify="left")
        self.tw["pl_status"].pack(anchor="w", padx=10)
        self.pl_fill()

    def pl_fill(self):
        tree = self.tw.get("pl_tree")
        if tree is None:
            return
        tree.delete(*tree.get_children())
        if not PLACEMENTS_DIR.is_dir():
            return
        for p in sorted(PLACEMENTS_DIR.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                n = len(data.get("events", [])) if isinstance(data, dict) else len(data)
            except Exception:
                n = "?"
            tree.insert("", "end", iid=p.stem, values=(p.stem, n,
                                                        time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))))

    def pl_rerecord(self):
        tree = self.tw.get("pl_tree")
        sel = tree.selection() if tree is not None else ()
        if not sel:
            self.set_text("pl_status", "Pick a map first.")
            return
        src = PLACEMENTS_DIR / f"{sel[0]}.json"
        old = PLACEMENTS_DIR / "_old"
        try:
            old.mkdir(exist_ok=True)
            dest = old / f"{sel[0]}_{time.strftime('%Y%m%d_%H%M%S')}.json"
            src.replace(dest)
        except OSError as e:
            self.set_text("pl_status", f"Couldn't move it: {e}", self.C["red"])
            return
        self.set_text("pl_status", f"Moved to placements/_old/{dest.name}. The next run on '{sel[0]}' will ask "
                                   "you to place your units again.")
        self.pl_fill()

    # ============================================================ Profiles tab
    def build_profiles_tab(self, f):
        C = theme.palette(self.C)
        self.label(f, "A profile is a saved copy of your settings (tuning, stops, plan, safety, alerts "
                      "except the webhook). Handy for 'overnight raid' vs 'quick challenges'.",
                   wraplength=740, justify="left").pack(anchor="w", padx=10, pady=8)
        lb = tk.Listbox(f, height=10, exportselection=False)
        lb.pack(fill="x", padx=10)
        self.tw["pr_list"] = lb
        row = tk.Frame(f, bg=C["screen"])
        row.pack(fill="x", padx=10, pady=8)
        self.label(row, "Name:", bold=True).pack(side="left")
        v = tk.StringVar(master=f)
        self.tool_vars.append(v)
        self.tw["pr_name"] = v
        tk.Entry(row, textvariable=v, width=26).pack(side="left", padx=6)
        self.button_(row, "Save current settings", self.pr_save, C["green"]).pack(side="left")
        self.button_(row, "Load selected", self.pr_load).pack(side="left", padx=6)
        self.button_(row, "Delete selected", self.pr_delete, C["amber"]).pack(side="left")
        self.tw["pr_status"] = self.label(f, "", wraplength=740, justify="left")
        self.tw["pr_status"].pack(anchor="w", padx=10)
        lb.bind("<<ListboxSelect>>", lambda e: self.pr_pick())
        self.pr_fill()

    def pr_files(self):
        return sorted(PROFILES_DIR.glob("*.json")) if PROFILES_DIR.is_dir() else []

    def pr_fill(self):
        lb = self.tw.get("pr_list")
        if lb is None:
            return
        lb.delete(0, "end")
        for p in self.pr_files():
            lb.insert("end", p.stem)

    def pr_pick(self):
        lb = self.tw.get("pr_list")
        sel = lb.curselection() if lb is not None else ()
        if sel:
            self.tw["pr_name"].set(lb.get(sel[0]))

    def pr_save(self):
        name = common.slug(self.tw["pr_name"].get())
        if not name:
            self.set_text("pr_status", "Type a name first.")
            return
        data = {k: v for k, v in self.settings.items() if k not in PROFILE_SKIP}
        try:
            PROFILES_DIR.mkdir(exist_ok=True)
            common.write_json_atomic(PROFILES_DIR / f"{name}.json", data, indent=2)
        except OSError as e:
            self.set_text("pr_status", f"Couldn't save: {e}", self.C["red"])
            return
        self.set_text("pr_status", f"Saved profile '{name}'.")
        self.pr_fill()

    def pr_load(self):
        lb = self.tw.get("pr_list")
        sel = lb.curselection() if lb is not None else ()
        if not sel:
            self.set_text("pr_status", "Pick a profile first.")
            return
        name = lb.get(sel[0])
        try:
            data = json.loads((PROFILES_DIR / f"{name}.json").read_text(encoding="utf-8"))
        except Exception as e:
            self.set_text("pr_status", f"Couldn't read it: {e}", self.C["red"])
            return
        if hasattr(self, "close_settings") and getattr(self, "settings_window", None) is not None:
            self.close_settings()          # its widgets would show the old values
        for k, v in data.items():
            if k in self.settings and k not in PROFILE_SKIP:
                self.set_setting(k, v)
        self.set_text("pr_status", f"Loaded '{name}'. Settings that apply on start take effect at the next Start.")

    def pr_delete(self):
        lb = self.tw.get("pr_list")
        sel = lb.curselection() if lb is not None else ()
        if not sel:
            return
        try:
            (PROFILES_DIR / f"{lb.get(sel[0])}.json").unlink()
        except OSError:
            pass
        self.pr_fill()

    # ============================================================ Remote tab + dashboard
    def build_remote_tab(self, f):
        C = theme.palette(self.C)
        self.label(f, "Watch and control BMO from a phone or another PC through a small web page. It is OFF by "
                      "default. Anyone with the link can pause, stop and start your macro, so the link contains a "
                      "secret token. Local-only works on this PC. For your phone, tick 'other devices' and use "
                      "Tailscale (recommended) or your home Wi-Fi. Never port-forward this to the internet: "
                      "it is plain HTTP.", wraplength=740, justify="left", fg=C["amber"]).pack(anchor="w", padx=10, pady=8)
        self.check(f, "Turn the remote page on", "dash_enabled", refs=self.tool_vars, after=self.dash_apply)
        self.check(f, "Allow other devices on my network (otherwise this PC only)", "dash_lan",
                   refs=self.tool_vars, after=self.dash_apply)
        row = tk.Frame(f, bg=C["screen"])
        row.pack(fill="x", padx=10, pady=4)
        self.label(row, "Port:", bold=True).pack(side="left")
        pv = tk.StringVar(master=f, value=str(self.settings.get("dash_port", 8765)))
        self.tool_vars.append(pv)
        tk.Entry(row, textvariable=pv, width=7).pack(side="left", padx=6)
        self.tw["dash_port"] = pv
        self.button_(row, "Apply", self.dash_apply_port, C["green"]).pack(side="left")
        self.button_(row, "New token", self.dash_new_token, C["amber"]).pack(side="left", padx=6)
        self.tw["dash_status"] = self.label(f, "", wraplength=740, justify="left")
        self.tw["dash_status"].pack(anchor="w", padx=10, pady=4)
        self.tw["dash_links"] = tk.Text(f, height=6, wrap="word", bg=theme.T["paper_lt"], fg=C["ink"], font=("Consolas", 9),
                                        relief="flat")
        self.tw["dash_links"].pack(fill="x", padx=10)
        row = tk.Frame(f, bg=C["screen"])
        row.pack(fill="x", padx=10, pady=6)
        self.button_(row, "Copy link", self.dash_copy).pack(side="left")
        self.button_(row, "Open in browser", self.dash_open).pack(side="left", padx=6)
        self.dash_refresh_tab()

    def dash_links(self):
        token = self.settings.get("dash_token") or ""
        port = int(self.settings.get("dash_port", 8765))
        links = [f"http://127.0.0.1:{port}/?t={token}"]
        if self.settings.get("dash_lan"):
            links += [f"http://{ip}:{port}/?t={token}" for ip in local_addresses()]
        return links

    def dash_refresh_tab(self):
        if self.dash is not None:
            status, fg = f"ON - listening on port {self.settings.get('dash_port', 8765)}.", self.C["green"]
        elif self.dash_error:
            status, fg = f"Couldn't start: {self.dash_error}", self.C["red"]
        else:
            status, fg = "OFF.", theme.palette(self.C)["ink"]
        self.set_text("dash_status", status, fg)
        t = self.tw.get("dash_links")
        if t is not None:
            try:
                t.configure(state="normal")
                t.delete("1.0", "end")
                if self.dash is not None:
                    t.insert("end", "\n".join(self.dash_links()))
                t.configure(state="disabled")
            except tk.TclError:
                pass

    def dash_apply_port(self):
        try:
            port = int(self.tw["dash_port"].get())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.set_text("dash_status", "The port must be a number from 1024 to 65535.", self.C["red"])
            return
        self.set_setting("dash_port", port)
        self.dash_apply()

    def dash_new_token(self):
        self.set_setting("dash_token", secrets.token_urlsafe(18))
        self.dash_refresh_tab()
        self.log("Remote page: new token created. Old links stop working.")

    def dash_copy(self):
        links = self.dash_links()
        if links and self.dash is not None:
            self.root.clipboard_clear()
            self.root.clipboard_append(links[-1])
            self.set_text("dash_status", "Link copied.", self.C["green"])

    def dash_open(self):
        if self.dash is not None:
            webbrowser.open(self.dash_links()[0])

    def dash_apply(self):
        """Start, restart or stop the web server to match the settings."""
        if self.dash is not None:
            self.dash.stop()
            self.dash = None
        self.dash_error = ""
        if self.settings.get("dash_enabled"):
            if not self.settings.get("dash_token"):
                self.settings["dash_token"] = secrets.token_urlsafe(18)
                self.schedule_save()
            host = "0.0.0.0" if self.settings.get("dash_lan") else "127.0.0.1"
            port = int(self.settings.get("dash_port", 8765))
            try:
                srv = DashServer(self, host, port)
                srv.start()
                self.dash = srv
                self.log(f"Remote page on at port {port}" + (" (all devices)." if host == "0.0.0.0" else " (this PC only)."))
            except OSError as e:
                self.dash_error = str(e)
                self.log(f"Remote page couldn't start: {e}")
        self.dash_refresh_tab()

    def dash_stop(self):
        if self.dash is not None:
            self.dash.stop()
            self.dash = None

    def dash_tick(self):
        """Called from poll(): run queued remote commands and refresh the snapshot the web thread serves."""
        try:
            while True:
                item = self.remote_cmds.get_nowait()
                cmd, value = item["cmd"], item.get("value")
                self.log(f"Remote: {cmd}" + (f" {value}" if value else ""))
                if cmd == "start":
                    self.start()
                elif cmd == "pause":
                    self.pause()
                elif cmd == "resume":
                    self.resume()
                elif cmd == "stop":
                    self.stop("user")
                elif cmd == "adventure":
                    self.set_adventure(value)
                elif cmd == "snapshot":
                    if self.running:
                        self.send_control("SNAPSHOT")
                    else:
                        self.run_oneshot("snap", ["--snap"], timeout=60)
        except queue.Empty:
            pass
        if self.dash is None or time.monotonic() - self.dash_last < 1.0:
            return
        self.dash_last = time.monotonic()
        try:
            shot_t = int(LIVE_SHOT.stat().st_mtime)
        except OSError:
            shot_t = 0
        self.dash_state = {
            "status": self.status_text(), "activity": self.activity, "wave": self.wave,
            "adventure": self.adv_label(self.active_mode or self.adv_key).title(),
            "selected": self.adv_key, "elapsed": self.fmt(self.elapsed), "runs": self.total_runs,
            "failures": self.failures, "plan": self.plan_or_limits_text(),
            "adventures": [{"key": a["key"], "label": a["label"].title()} for a in self.advs],
            "log": list(self.logs)[-12:], "shot_t": shot_t, "running": self.running,
        }
