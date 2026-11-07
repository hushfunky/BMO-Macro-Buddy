# BMO Macro Buddy

An overlay + macro that automates Candy-Kingdom-Defense farming in Roblox
(Dungeon Train challenges, selectable worlds/acts/Endless modes, Lich raids, custom routines).
It reads the screen with image matching and sends keyboard/mouse input. It does **not** touch
the game's memory or files.(i made this out of boredom so dont expect it to be some grand macro)

## Before you start
BMO matches pictures of the game's buttons. Put the **`templates/`** folder (the pictures) beside `macro.py`; `refs/` (map reference views) and `routines/` are optional extras that go in the same place. Your own settings, placements, logs and stats are created on your PC the first time you run it.

## Setup (do these in order)
1. Use **Windows 10/11** with **64-bit Python 3.10+** from python.org, with "Add Python to PATH" ticked.
2. Run `install.bat` once, then `start_bmo.bat`.
3. Put Roblox on your **main display** at **1280x720 or 1920x1080**. Pick windowed or fullscreen and stay with it.
4. Set Windows Settings > Display > Scale to **100%**.
5. Leave Roblox graphics and UI settings at defaults, and keep the same ones between runs.
6. Click **CHECK SETUP** and fix anything it flags before starting.(some placements will be flagged but thats fine)
7. On a new map, place your units once and press **F8**. BMO replays them afterwards.
8. Don't resize or move the Roblox window during a run, and keep it in front.
9. Optional: add a Discord webhook in Settings for phone alerts, and never share that file.
10. Watch the first run. If it fails, send the log plus the newest picture in `debug/`.

## World / Act adventure
Pick **WORLD / ACT / ENDLESS** as the adventure (or put `world:3` in a plan). BMO > Settings > Macro chooses a world and
then Act 1-5 or Endless. It goes Portal > world > stage > Play > Start. Numbered acts use **Play Again** between runs;
when the run/plan limit is reached, BMO uses **Return to Hub** instead. Each world/act gets its own saved unit placements
(record once, F8).

Endless modes loop the same way each run. Set **Endless target wave** to a positive number to return
home at that wave, or set it to **0** to continue until the player is defeated. The macro then starts another Endless run
unless a stop condition or plan says otherwise.

Pictures: the main `templates/` folder is used for 1280x720; `templates/1920x1080/` is preferred at 1920x1080.
`<world>_world.png` identifies the world card (candy, ice, fire, lemon, crystal, factory, nightosphere, lumpy, wizard). Numbered act
cards use `act_<world>_<n>.png` (the stage-name text, not the potentially covered ACT number). `act_<n>_check.png` and
`act_endless_check.png` recognize an already-selected stage; `act_endless.png` identifies the Endless card; and
`act_play_again.png` identifies the replay button. `lobby_2.png` is the Lobby button when the word LOBBY isn't showing.

## Resolution
**Supported:** 1280x720 and 1920x1080. CHECK SETUP says "Supported" for these and "Unsupported, may not work"
for anything else (other 16:9 sizes often work, but they aren't tested).
BMO works on a virtual 1280-wide screen: it shrinks or enlarges what it sees to match the built-in pictures,
and scales mouse positions back to your real screen. 1280x720 behaves exactly as before.
- The size each picture is drawn at is learned on the first run and saved in `scale_hints.json`
  (per resolution), so later launches skip the search. Delete that file to relearn.
- Override with the environment variable `BMO_UI_SCALE`: `off` disables it, `1.5` forces a number.
- Do not resize the Roblox window mid-run, and re-record unit placements if you change size.
- Template images you capture yourself are saved at the normalized size, so they work at any resolution.

## Per-resolution pictures (optional)
Most pictures work at any size. A few (the E prompts, Play buttons) are drawn at a fixed pixel size by Roblox.
If one of those doesn't match at your resolution, capture it there: set Roblox to that resolution, open
BMO > TOOLS > Templates > capture, drag a box around it and give it the SAME name as the original.
It is saved into `templates/<width>x<height>/` (for example `templates/1920x1080/play.png`) and used
automatically at that resolution; anything missing there falls back to the main `templates/` folder.

## First run
- Pick an adventure, press Start, then click into Roblox.
- On a map with no saved placements, place your units once and press **F8**. BMO replays them afterwards.
- Hotkeys: **F6** click-through, **F7** hide/show, **F8** finish placement, **F10** pause (changeable in settings).

## Optional: phone alerts
Settings -> paste a **Discord webhook URL** (Discord channel settings -> Integrations -> Webhooks).
It is saved only on your PC in `bmo_settings.json`. **Never share that file or the URL.**

## Files
| File | What it does |
|---|---|
| `ckd_launcher.py` (older copies: `adventure_time_launcher.py`) | The BMO overlay (start here; `start_bmo.bat` finds it) |
| `bmo_theme.py` | The Adventure Time look (colours, buttons, switches, sliders, banners) shared by every window. Edit the `T` colour dict at the top to retune |
| `macro.py` | The macro itself |
| `bmo_common.py`, `bmo_tools.py`, `bmo_calc.py` | Shared code, tools window, unit calculator(shoutout Dill, havent update this because i didnt ask for his permission, so if i do update itll be my own) |
| `bmo_wave_glyphs.py` | The letter shapes macro.py uses to read the wave counter (data only) |
| `bmo_assist.py` | Optional AI "what screen is this?" helper (off by default) |
| `scale_hints.json` | Created automatically: learned picture sizes per resolution |
| `units.json`, `zones.json` | Game data / placement zones |
| `templates/`, `placements/`, `routines/` | Screenshots, saved placements, Lich raids, custom routines |

## Optional: AI helper (off by default)
If the macro is stuck for two minutes while trying to get back into the game, BMO can send ONE small picture
of the Roblox window to a Claude vision model and log what it thinks the screen is (lobby, disconnected,
loading, pop-up...). It only reports; it never clicks. To enable it, create `ai_assist.json` beside `macro.py`:
`{"enabled": true, "api_key": "sk-ant-...", "model": "claude-haiku-5-5"}`. It uses your own API key, is limited
to a few calls per session, and the file must never be shared. See the top of `bmo_assist.py` for details.(im ngl you prob wont need this and dont expect me to put time into it because i havent tested it fr)

## If a saved file gets damaged
BMO saves settings, stats, placements and routines by writing a temp file and swapping it in, so a crash can't leave
half a file. If a file is ever unreadable anyway, BMO keeps it as `<name>.bad` (nothing is deleted) and starts that
one fresh; for a map's placements it simply records the map again.

## For developers
`python -m unittest discover tests` runs the logic tests (plan parsing, routine checks, safe saving). They need no
Windows or Roblox.

## Use at your own risk
Automation may be against Roblox's or the game's rules and could get an account actioned.
Use it on an alt, not your main. Provided as-is with no warranty.
