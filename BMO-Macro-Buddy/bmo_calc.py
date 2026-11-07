"""BMO Unit Calculator - the "Make a Team" sheet from Unit Calculator V1.6 (made by Dill) as a BMO window.

Keep this file and units.json beside adventure_time_launcher.py. Open it from BMO:
  * LOG panel -> CALC button, or
  * the adventure button's list -> "Unit Calculator..."
It also runs on its own:  python bmo_calc.py

Same maths as the spreadsheet:
  stat = base stat x (1 + roll bonus) x (1 + trait bonus)   [damage also x (1 + 0.04 x (level - 1))]
  SPA (attack delay) is divided by (1 + bonus) instead, so a bigger bonus means faster attacks.
  DPS each = fully upgraded damage / fully upgraded SPA;  DPS (all placed) = DPS each x units placed.
Your team is saved to bmo_calc_team.json after every change.
"""
import json, math, tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

import bmo_theme as theme

SLOTS = 6
HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "units.json"
TEAM_FILE = HERE / "bmo_calc_team.json"

_DEFAULT_COLORS = {"body": "#55B8B0", "dark": "#287E80", "light": "#9CE0D5", "screen": "#DDF7E9",
                   "ink": "#315B54", "cream": "#FFF0C6", "blue": "#286E91", "red": "#A63848",
                   "green": "#327C4B", "yellow": "#F7D56B", "white": "#FFFFFF", "amber": "#9A6B12"}

# the team that was in the spreadsheet when it was sent over; used only until a team has been saved
_START_TEAM = [
    ("Game King", "Z", "Z", "Z"), ("Vampire King", "Z", "S-", "S"), ("Belly Demon", "Z", "A", "A-"),
    ("Prism Unicorn", "Z", "Z", "Z"), ("Vampire Beast", "Z", "Z", "Z"), ("Grim Reaper", "S+", "A+", "A+"),
]


def blank_slot():
    return {"unit": "", "trait": "None", "level": "50", "gd": "F", "gr": "F", "gs": "F", "placed": ""}


def start_team():
    team = []
    for unit, gd, gr, gs in _START_TEAM:
        s = blank_slot()
        s.update(unit=unit, trait="Oh My Glob", gd=gd, gr=gr, gs=gs)
        team.append(s)
    return team


class CalcData:
    """The unit / grade / trait tables (exported from the spreadsheet into units.json) and the maths."""

    def __init__(self, path=DATA_FILE):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.step = float(raw.get("level_step", 0.04))
        self.max_level = int(raw.get("max_level", 50))
        self.grades_list = raw["grades"]
        self.grades = {g["grade"].upper(): float(g["bonus"]) for g in self.grades_list}
        self.traits_list = raw["traits"]
        self.traits = {t["name"]: t for t in self.traits_list}
        self.units_list = raw["units"]
        self.units = {u["name"]: u for u in self.units_list}

    def roll(self, value):
        """A grade like 'S+' or a plain number (0.35) -> the bonus, same as the sheet's GradeTable lookup."""
        s = str(value).strip()
        if not s:
            return 0.0
        try:
            v = float(s)
            return v if math.isfinite(v) and -0.9 <= v <= 10 else 0.0
        except ValueError:
            return self.grades.get(s.upper(), 0.0)

    def max_placed(self, name):
        u = self.units.get(name)
        try:
            return max(1, int(float(u["max_placed"]))) if u else 1
        except (TypeError, ValueError):
            return 1

    def compute(self, slot):
        u = self.units.get(slot.get("unit", ""))
        if not u:
            return None
        t = self.traits.get(slot.get("trait", "None")) or {"dmg": 0, "rng": 0, "spa": 0}
        try:
            lvl = min(self.max_level, max(1, int(float(str(slot.get("level", "")).strip() or self.max_level))))
        except ValueError:
            lvl = self.max_level
        rd, rr, rs = (self.roll(slot.get(k, "")) for k in ("gd", "gr", "gs"))
        lf = 1 + self.step * (lvl - 1)
        fd = (1 + rd) * (1 + float(t["dmg"])) * lf
        fr = (1 + rr) * (1 + float(t["rng"]))
        fs = (1 + rs) * (1 + float(t["spa"]))
        card = (u["dmg0"] * fd, u["rng0"] * fr, u["spa0"] / fs)
        mx = (u["dmg"] * fd, u["rng"] * fr, u["spa"] / fs)
        cap = self.max_placed(u["name"])
        try:
            placed = min(cap, max(0, int(float(str(slot.get("placed", "")).strip()))))
        except ValueError:
            placed = cap
        each = mx[0] / mx[2] if mx[2] else 0.0
        return {"unit": u, "level": lvl, "card": card, "max": mx, "placed": placed, "cap": cap,
                "each": each, "all": each * placed}


def n0(v):
    return f"{v:,.0f}"


def info_text(u):
    bits = []
    if u.get("active"):
        bits.append(f"Active: {u['active']}.")
    if u.get("passive"):
        bits.append(f"Passive: {u['passive']}.")
    if u.get("special"):
        bits.append(str(u["special"]))
    return "  ".join(bits)


class UnitCalculator:
    TEAM_COLS = (("unit", "Unit", 120, "w"), ("placed", "Placed", 52, "e"),
                 ("cd", "Card DMG", 78, "e"), ("cr", "Card RNG", 66, "e"), ("cs", "Card SPA", 66, "e"),
                 ("md", "Max DMG", 84, "e"), ("mr", "Max RNG", 66, "e"), ("ms", "Max SPA", 66, "e"),
                 ("each", "DPS each", 84, "e"), ("all", "DPS (all)", 90, "e"),
                 ("share", "Share", 56, "e"), ("rank", "Rank", 44, "e"))

    def __init__(self, root, colors=None, here=None, pos=(120, 80)):
        self.root = root
        self.C = dict(_DEFAULT_COLORS, **(colors or {}))
        self.C.update(screen=theme.T["paper"], ink=theme.T["brown"])     # Adventure Time paper
        self.here = Path(here) if here else HERE
        self.team_file = self.here / "bmo_calc_team.json"
        self.pos = pos
        self.win = None
        self.data = None
        self.vars = []
        self.job = None
        self.save_job = None
        self.loading = False
        self.sort_state = ("dps", True)

    # ---------------------------------------------------------------- window
    def show(self):
        if self.win is not None:
            try:
                if self.win.winfo_exists():
                    self.win.deiconify()
                    self.win.lift()
                    return
            except tk.TclError:
                pass
            self.win = None
        try:
            self.data = CalcData(self.here / "units.json")
        except Exception as e:
            messagebox.showerror("Unit Calculator",
                                 f"Couldn't read units.json beside the launcher:\n{e}", parent=self.root)
            return
        C = self.C
        w = tk.Toplevel(self.root)
        self.win = w
        w.title("BMO's Unit Calculator")
        w.geometry(f"1010x650+{self.pos[0]}+{self.pos[1]}")
        w.minsize(940, 600)
        w.configure(bg=C["screen"])
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())

        style = ttk.Style(w)
        style.configure("Calc.Treeview", font=("Consolas", 9), rowheight=22,
                        background=theme.T["paper_lt"], fieldbackground=theme.T["paper_lt"], foreground=C["ink"])
        style.configure("Calc.Treeview.Heading", font=("Comic Sans MS", 8, "bold"))

        theme.Banner(w, "UNIT CALCULATOR", bg=C["screen"]).pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(w, text="from Unit Calculator V1.6 (made by Dill)  -  your team saves automatically",
                 bg=C["screen"], fg=C["ink"], font=("Arial", 8)).pack(pady=(0, 4))
        nb = ttk.Notebook(w)
        nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        team = tk.Frame(nb, bg=C["screen"])
        units = tk.Frame(nb, bg=C["screen"])
        guide = tk.Frame(nb, bg=C["screen"])
        nb.add(team, text="  Team  ")
        nb.add(units, text="  Unit list  ")
        nb.add(guide, text="  Grades + traits  ")
        self.build_team(team)
        self.build_units(units)
        self.build_guide(guide)
        self.load_team()
        self.recalc()

    def close(self):
        if self.job is not None:
            try:
                self.root.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.save_job is not None:
            try:
                self.root.after_cancel(self.save_job)
            except Exception:
                pass
            self.save_job = None
            self.save_team()
        w, self.win = self.win, None
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
        self.vars = []          # drop the Tk variables here, on the Tk thread, after the window is gone

    # ------------------------------------------------------------ team tab
    def build_team(self, parent):
        C, d = self.C, self.data
        head = tk.Frame(parent, bg=C["screen"])
        head.pack(fill="x", padx=10, pady=(8, 0))
        for col, (text, width) in enumerate((("Slot", 4), ("Unit", 20), ("Rarity", 15), ("Trait", 12),
                                              ("Level", 6), ("DMG grade", 9), ("RNG grade", 9),
                                              ("SPA grade", 9), ("Placed", 7))):
            tk.Label(head, text=text, width=width, anchor="w", bg=C["screen"], fg=C["ink"],
                     font=("Comic Sans MS", 8, "bold")).grid(row=0, column=col, padx=2)
        grade_names = [g["grade"] for g in d.grades_list]
        trait_names = [t["name"] for t in d.traits_list]
        unit_names = [u["name"] for u in d.units_list]
        self.slots = []
        for i in range(SLOTS):
            v = {k: tk.StringVar() for k in ("unit", "trait", "level", "gd", "gr", "gs", "placed")}
            self.vars.extend(v.values())
            tk.Label(head, text=str(i + 1), width=4, anchor="w", bg=C["screen"], fg=C["ink"],
                     font=("Consolas", 9, "bold")).grid(row=i + 1, column=0, padx=2, pady=2)
            cb = ttk.Combobox(head, textvariable=v["unit"], values=[""] + unit_names, width=20, state="readonly")
            cb.grid(row=i + 1, column=1, padx=2)
            rarity = tk.Label(head, text="", width=15, anchor="w", bg=C["screen"], fg=C["ink"], font=("Arial", 8))
            rarity.grid(row=i + 1, column=2, padx=2)
            ttk.Combobox(head, textvariable=v["trait"], values=trait_names, width=12,
                         state="readonly").grid(row=i + 1, column=3, padx=2)
            ttk.Spinbox(head, textvariable=v["level"], from_=1, to=d.max_level, width=5,
                        command=self.queue_recalc).grid(row=i + 1, column=4, padx=2)
            for col, k in ((5, "gd"), (6, "gr"), (7, "gs")):
                ttk.Combobox(head, textvariable=v[k], values=grade_names, width=7).grid(row=i + 1, column=col, padx=2)
            ttk.Spinbox(head, textvariable=v["placed"], from_=0, to=20, width=5,
                        command=self.queue_recalc).grid(row=i + 1, column=8, padx=2)
            v["unit"].trace_add("write", lambda *a, idx=i: self.on_unit_change(idx))
            for k in ("trait", "level", "gd", "gr", "gs", "placed"):
                v[k].trace_add("write", lambda *a: self.queue_recalc())
            self.slots.append({"vars": v, "rarity": rarity})

        bar = tk.Frame(parent, bg=C["screen"])
        bar.pack(fill="x", padx=10, pady=(8, 2))
        self.total_label = tk.Label(bar, text="TEAM DPS  0", bg=C["screen"], fg=C["green"],
                                    font=("Comic Sans MS", 14, "bold"))
        self.total_label.pack(side="left")
        tk.Label(bar, text="(fully upgraded, with your rolls, traits and levels, every copy placed)",
                 bg=C["screen"], fg=C["ink"], font=("Arial", 8)).pack(side="left", padx=8)
        for text, fn, color in (("CLEAR", self.clear_team, C["red"]), ("RESET TO SHEET TEAM", self.reset_team, C["blue"])):
            theme.Pill(bar, text, fn, "red" if color == C["red"] else "blue", height=28, size=8,
                       bg=C["screen"]).pack(side="right", padx=3)

        cols = [c[0] for c in self.TEAM_COLS]
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=SLOTS, style="Calc.Treeview")
        for key, title, width, anchor in self.TEAM_COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=key == "unit")
        self.tree.pack(fill="x", padx=10, pady=(2, 6))
        tk.Label(parent, text="Card = what the unit card shows in the lobby (not upgraded).  Max = fully upgraded in a match.",
                 bg=C["screen"], fg=C["ink"], font=("Arial", 8)).pack(anchor="w", padx=12)
        tk.Label(parent, text="ABILITIES", bg=C["screen"], fg=C["ink"],
                 font=("Comic Sans MS", 9, "bold")).pack(anchor="w", padx=12, pady=(6, 0))
        box = tk.Frame(parent, bg=C["screen"])
        box.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.info = tk.Text(box, height=8, wrap="word", bg=theme.T["paper_lt"], fg=C["ink"], font=("Consolas", 9),
                            relief="flat", state="disabled")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.info.yview)
        self.info.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.info.pack(side="left", fill="both", expand=True)

    def on_unit_change(self, idx):
        if self.loading:
            return
        v = self.slots[idx]["vars"]
        name = v["unit"].get()
        # a new unit starts with all of its copies placed (blank = max)
        v["placed"].set("" if not name else str(self.data.max_placed(name)))

    def queue_recalc(self):
        if self.win is None or self.loading:
            return
        if self.job is not None:
            try:
                self.root.after_cancel(self.job)
            except Exception:
                pass
        self.job = self.root.after(80, self.recalc)

    def slot_values(self, idx):
        return {k: var.get() for k, var in self.slots[idx]["vars"].items()}

    def recalc(self):
        self.job = None
        if self.win is None:
            return
        d = self.data
        results = []
        for i in range(SLOTS):
            s = self.slot_values(i)
            r = d.compute(s)
            results.append(r)
            u = r["unit"] if r else None
            self.slots[i]["rarity"].config(text=(u["rarity"] if u else ""))
        total = sum(r["all"] for r in results if r)
        self.total_label.config(text=f"TEAM DPS  {n0(total)}")
        for item in self.tree.get_children():
            self.tree.delete(item)
        alls = [r["all"] for r in results if r]
        for r in results:
            if not r:
                continue
            rank = 1 + sum(1 for a in alls if a > r["all"])
            share = f"{r['all'] / total * 100:.1f}%" if total else ""
            self.tree.insert("", "end", values=(
                r["unit"]["name"], f"{r['placed']}/{r['cap']}",
                n0(r["card"][0]), f"{r['card'][1]:.1f}", f"{r['card'][2]:.3f}",
                n0(r["max"][0]), f"{r['max'][1]:.1f}", f"{r['max'][2]:.3f}",
                n0(r["each"]), n0(r["all"]), share, rank))
        lines = []
        for i, r in enumerate(results):
            if not r:
                continue
            u = r["unit"]
            splash = f"  [splash {u['splash']}]" if str(u.get("splash") or "") not in ("", "None") else ""
            lines.append(f"{i + 1}. {u['name']} ({u['rarity']}){splash}")
            extra = info_text(u)
            if extra:
                lines.append("     " + extra)
        self.info.config(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", "\n".join(lines) if lines else "Pick a unit in a slot to see its numbers.")
        self.info.config(state="disabled")
        self.queue_save()

    # --------------------------------------------------------- save / load
    def queue_save(self):
        if self.loading:
            return
        if self.save_job is not None:
            try:
                self.root.after_cancel(self.save_job)
            except Exception:
                pass
        self.save_job = self.root.after(500, self.save_now)

    def save_now(self):
        self.save_job = None
        self.save_team()

    def save_team(self):
        if not self.vars or self.win is None:
            return
        try:
            team = [self.slot_values(i) for i in range(SLOTS)]
            self.team_file.write_text(json.dumps({"team": team}, indent=1), encoding="utf-8")
        except Exception:
            pass

    def set_team(self, team):
        self.loading = True
        try:
            for i in range(SLOTS):
                s = blank_slot()
                if i < len(team):
                    s.update({k: str(v) for k, v in team[i].items() if k in s})
                if s["unit"] not in self.data.units:
                    s["unit"] = ""
                if s["trait"] not in self.data.traits:
                    s["trait"] = "None"
                if s["unit"] and not s["placed"].strip():
                    s["placed"] = str(self.data.max_placed(s["unit"]))
                for k, var in self.slots[i]["vars"].items():
                    var.set(s[k])
        finally:
            self.loading = False

    def load_team(self):
        team = None
        try:
            team = json.loads(self.team_file.read_text(encoding="utf-8")).get("team")
        except Exception:
            pass
        self.set_team(team if isinstance(team, list) else start_team())

    def clear_team(self):
        self.set_team([blank_slot() for _ in range(SLOTS)])
        self.recalc()

    def reset_team(self):
        self.set_team(start_team())
        self.recalc()

    # ---------------------------------------------------------- unit list
    UNIT_COLS = (("unit", "Unit", 140, "w"), ("rarity", "Rarity", 110, "w"), ("max", "Max", 40, "e"),
                 ("splash", "Splash", 60, "e"), ("dmg0", "Base DMG", 80, "e"), ("rng0", "Base RNG", 70, "e"),
                 ("spa0", "Base SPA", 70, "e"), ("dmg", "Maxed DMG", 90, "e"), ("rng", "Maxed RNG", 76, "e"),
                 ("spa", "Maxed SPA", 76, "e"), ("dps", "Maxed DPS", 90, "e"))

    def build_units(self, parent):
        C = self.C
        top = tk.Frame(parent, bg=C["screen"])
        top.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(top, text="Search", bg=C["screen"], fg=C["ink"], font=("Comic Sans MS", 9, "bold")).pack(side="left")
        self.search = tk.StringVar()
        self.vars.append(self.search)
        tk.Entry(top, textvariable=self.search, width=24).pack(side="left", padx=6)
        self.search.trace_add("write", lambda *a: self.fill_units())
        tk.Label(top, text="Base = before roll, trait and level.  Click a heading to sort.",
                 bg=C["screen"], fg=C["ink"], font=("Arial", 8)).pack(side="left", padx=8)
        self.utree = ttk.Treeview(parent, columns=[c[0] for c in self.UNIT_COLS], show="headings",
                                  style="Calc.Treeview")
        for key, title, width, anchor in self.UNIT_COLS:
            self.utree.heading(key, text=title, command=lambda k=key: self.sort_units(k))
            self.utree.column(key, width=width, anchor=anchor, stretch=key == "unit")
        sb = ttk.Scrollbar(parent, orient="vertical", command=self.utree.yview)
        self.utree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=(2, 8))
        self.utree.pack(fill="both", expand=True, padx=(10, 0), pady=(2, 8))
        self.fill_units()

    def sort_units(self, key):
        same = self.sort_state[0] == key
        self.sort_state = (key, not self.sort_state[1] if same else key != "unit")
        self.fill_units()

    def fill_units(self):
        if self.win is None:
            return
        needle = self.search.get().strip().lower()
        rows = []
        for u in self.data.units_list:
            if needle and needle not in u["name"].lower() and needle not in str(u["rarity"]).lower():
                continue
            dps = u["dmg"] / u["spa"] if u["spa"] else 0
            rows.append({"unit": u["name"], "rarity": u["rarity"], "max": self.data.max_placed(u["name"]),
                         "splash": u.get("splash") or "", "dmg0": u["dmg0"], "rng0": u["rng0"], "spa0": u["spa0"],
                         "dmg": u["dmg"], "rng": u["rng"], "spa": u["spa"], "dps": dps})
        key, desc = self.sort_state
        def sk(r):
            v = r[key]
            return (0, v) if isinstance(v, (int, float)) else (1, str(v).lower())
        if key != "unit" or not desc:
            rows.sort(key=sk, reverse=desc)
        else:
            rows.sort(key=sk)
        for item in self.utree.get_children():
            self.utree.delete(item)
        for r in rows:
            self.utree.insert("", "end", values=(
                r["unit"], r["rarity"], r["max"], r["splash"], n0(r["dmg0"]), f"{r['rng0']:.1f}", f"{r['spa0']:.3f}",
                n0(r["dmg"]), f"{r['rng']:.1f}", f"{r['spa']:.3f}", n0(r["dps"])))

    # ---------------------------------------------------------- guide tab
    def build_guide(self, parent):
        C, d = self.C, self.data
        t = tk.Text(parent, wrap="word", bg=theme.T["paper_lt"], fg=C["ink"], font=("Consolas", 9), relief="flat")
        t.pack(fill="both", expand=True, padx=10, pady=8)
        out = ["GRADES (what a roll gives you)", f"{'Grade':<7}{'Bonus':>7}   Tier range / odds"]
        for g in d.grades_list:
            extra = ""
            if g.get("range"):
                odds = f"{g['odds'] * 100:g}%" if g.get("odds") is not None else ""
                extra = f"{g['range']}   {odds}"
            out.append(f"{g['grade']:<7}{'+%g%%' % (g['bonus'] * 100):>7}   {extra}")
        out += ["", "TRAITS", f"{'Trait':<14}{'Damage':>8}{'Range':>8}{'Speed':>8}{'VIP odds':>10}{'No VIP':>9}"]
        pct = lambda v: f"{v * 100:g}%" if isinstance(v, (int, float)) else str(v)
        for tr in d.traits_list:
            out.append(f"{tr['name']:<14}{pct(tr['dmg']):>8}{pct(tr['rng']):>8}{pct(tr['spa']):>8}"
                       f"{pct(tr['vip']):>10}{pct(tr['novip']):>9}")
        out += ["", "Thrifty: costs 12% less to place (no stat change).", "",
                f"LEVEL: damage x (1 + {d.step:g} x (level - 1)). Range and SPA stay the same at every level.",
                "Speed bonuses divide SPA (the delay between attacks), so more speed = lower SPA.",
                "A grade box also accepts a plain number, e.g. 0.35 for +35%."]
        t.insert("1.0", "\n".join(out))
        t.config(state="disabled")


if __name__ == "__main__":
    r = tk.Tk()
    r.withdraw()
    calc = UnitCalculator(r)
    calc.show()
    if calc.win is not None:
        calc.win.protocol("WM_DELETE_WINDOW", lambda: (calc.close(), r.destroy()))
        r.mainloop()
    else:
        r.destroy()
