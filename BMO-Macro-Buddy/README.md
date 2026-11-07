# BMO Macro Buddy

An overlay + macro that automates Adventure Time tower-defense farming in Roblox
(Dungeon Train challenges, selectable worlds/acts/Endless modes, Lich raids, custom routines).
It reads the screen with image matching and sends keyboard/mouse input. It does **not** touch
the game's memory or files.

## Before you start
BMO matches pictures of the game's buttons. Put the **`templates/`** folder (the pictures) beside `macro.py`; `refs/` (map reference views) and `routines/` are optional extras that go in the same place. Your own settings, placements, logs and stats are created on your PC the first time you run it.

## Setup (do these in order)
1. Use **Windows 10/11** with **64-bit Python 3.10+** from python.org, with "Add Python to PATH" ticked.
2. Run `install.bat` once, then `start_bmo.bat`.
3. Put Roblox on your **main display** at **1280x720 or 1920x1080**. Pick windowed or fullscreen and stay with it.
4. Set Windows Settings > Display > Scale to **100%**.
5. Leave Roblox graphics and UI settings at defaults, and keep the same ones between runs.
6. Click **CHECK SETUP** and fix anything it flags before starting.
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
| `bmo_common.py`, `bmo_tools.py`, `bmo_calc.py` | Shared code, tools window, unit calculator |
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
to a few calls per session, and the file must never be shared. See the top of `bmo_assist.py` for details.

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


## Update 2: new worlds, Wish Run, shorter portal walk
* **Lumpy World / Wizard World** work like the other worlds: `lumpy_world.png` / `wizard_world.png` for the world card and
  `act_lumpy_<n>.png` / `act_wizard_<n>.png` for the acts (1-5).
* **Wish Run** is its own choice in the Act list (BMO > Settings > Macro > Act > "Wish Run (any world)"). The map is the same
  on every world, so BMO always uses the Candy Kingdom card (`candy_world.png`) and then `act_wish.png`. It plays like
  Infinite (Endless target wave applies), and its unit placements are saved under the map name `wish`.
  Every 5 waves a popup shows three wish cards. `wish_it.png` (the WISH IT! lettering) finds the buttons, the card colour gives
  the rarity (blue = Common, purple = Rare, gold = Legendary), and BMO clicks the best one: Legendary over Rare over Common,
  picking at random between cards of the same rarity. If the lettering isn't matched it falls back to finding the coloured cards.
* **Record key:** the key that finishes recording unit placement is F8 by default; change it in BMO > Settings > Overlay > HOTKEYS (a key name like f8, insert, home, or one letter). It applies while the macro runs.
* **Portal walk:** the macro now walks forward at most 2.4 seconds (it still stops the moment the Play screen shows). If the screen
  hasn't appeared it stands still for a few seconds (lag), then steps back and walks in again, up to 3 tries. Change
  `PORTAL_WALK_SECS` in macro.py to adjust it (now 2.4).

## Resolution lock
Click **LOG > CHECK SETUP** once with Roblox open. It saves the resolution it finds to `resolution_profile.json`, and every
Start after that uses it directly: no re-measuring the window, the matching `templates/<width>x<height>/` folder is used
straight away, all pictures are loaded and pre-sized during the Start countdown, and a picture that matches at its
remembered size (`scale_hints.json`) is not tried at other sizes. If the Roblox window no longer matches the saved size,
the log says so and BMO detects the resolution by itself until you run CHECK SETUP again. Run CHECK SETUP again whenever you
change resolution. Set `BMO_RES_LOCK=off` to turn the lock off.

## Lich raid
Pick **LICH RAID** as the adventure (or put `raid:5` in a plan). BMO opens Items (hotkey **B**; it clicks `items.png` if the
hotkey doesn't open it), waits for the Your Items popup (`your_items.png`), finds `lich_portal_icon.png` (scrolling the
popup down, then up, if it isn't showing), then presses `open_lich_portal.png` and `lich_enter.png`. After each battle it
presses `next_btn.png`, `open_lich.png` and `yes.png`, re-places your units and plays again. When the end screen shows
`none_left.png` it presses `back.png`, goes home, then carries on with the plan (or Dungeon Train if nothing is planned).
Tick **Settings > Stops > Stop when the Lich raid has no portals left** to stop instead. Placements are saved under the
map name `lich` (record once with F8).

**Going home:** wherever BMO returns to the hub it accepts any of `return_hub.png`, `lobby.png` or `lobby_2.png`
(the Lobby button also needs `go_home.png` for the confirmation). The routine step `go_home` works the same way.

## Update 3: small reliability fixes
* `start_bmo.bat` now finds the launcher whether it is called `ckd_launcher.py` or `adventure_time_launcher.py` (it used to fail if the name didn't match).
* Removed the `mss.mss is deprecated` warnings from the log (works on both old and new `mss`).
* Portal retry: when the Play screen doesn't show, BMO now steps back exactly as far as it walked, so every retry starts from the spawn spot instead of drifting further forward.

## Update 4: portal walk slider
* **BMO > Settings > Macro > Portal walk (seconds)** sets the most BMO walks forward to reach the portal (default **2.0**, range 1.0-3.5). It applies while the macro runs. The old fixed `PORTAL_WALK_SECS` in macro.py is now just the fallback.

## Update 5: faster Play Again
* In numbered acts BMO now looks for Play Again / Return to Hub first on every check, before the slower pop-up and wave-counter work, and waits 1.5s instead of 3s after pressing it.
* Before placing units it waits a little less (1.0s instead of 1.5s after Start Wave appears, 0.7s instead of 1.0s before pressing it).
* To go faster still, lower **Settings > Macro > Wait time multiplier** (it scales every pause; going under about 0.4 can miss pop-ups).

## Update 6: Lich raid end screen
* If Return to Hub shows on the raid end screen before Open Lich has appeared, BMO now waits up to 12 seconds for the Next button (or the none-left screen) instead of going straight home. If Next never shows it saves `debug/raid_end_no_next_*.png` and goes home as before; send me that picture and the log.
* The raid end-screen order is **Next -> Open Lich -> Yes**. The battle counts as won when Next shows; after Next, BMO waits for Open Lich, and if the none-left screen shows instead it presses Back, goes home and carries on with the plan.


## Update 3: bigger buttons, pop-ups, portal lag, walking in recordings
* **Extra pictures of one button.** Some buttons are drawn bigger sometimes (hovered by the mouse, selected, or on a
  last-act end screen that has no Next button). Save another picture of the same thing and BMO tries all of them:
  `act_play_again` also uses `play_again_2.png` (or `act_play_again_2.png`), `awesome` also uses `awesome_1.png`,
  `return_hub` also uses `return_hub_1.png` / `return_hub_2.png` / `return_home.png`. Any picture can have `_1` ... `_5`
  copies this way. These buttons are also searched at sizes from 0.75x to 1.45x, so a hovered button still matches.
* **Play Again (acts).** If Return to Hub is showing but Play Again isn't matched, BMO looks for Play Again for 5 more seconds
  (looser match, clearing pop-ups) before it goes home, and saves `debug/play_again_missing`.
* **AWESOME! unit pop-up after a Lich raid** is cleared before BMO looks for Next / Back / Return to Hub, and while it waits for them.
* **Return to Hub after the last Lich Portal.** BMO moves the mouse off the button first (a hovered button is bigger), searches
  wider sizes, then falls back to a looser match and finally to the green button's colour and shape.
* **Selected act card.** The act you picked is drawn bigger. `act_<world>_<n>` is now searched at larger sizes too, and the
  log says when the card looks already selected.
* **Portal lag.** If Areas won't open (the Play screen is covering it) or the Play screen never shows after walking, BMO walks
  forward a moment to get the main screen back, reopens Areas, presses the Portal and walks in again (3 tries).
* **Walking while recording placements.** The recorder now keeps W/A/S/D as "walks" (which keys, how long each was held; W+D
  together stays diagonal). On replay the keys are held the same way. If the character walks into a wall (the screen was
  scrolling, then stops) the keys are let go early. After each walk BMO saves a small picture next to the placement file
  (`<map>_view1.png` ...) so it can work out where the character ended up, and spots and zones placed after the walk follow it.
  Old recordings still work. Re-record a map to include its walking. Settings (environment variables): `BMO_WALL_STOP=0` turns
  wall detection off; `BMO_WALL_STALL` (0.5 s), `BMO_WALL_MOTION` (3.0) and `BMO_WALL_STILL` (1.2) tune it. Spots the game
  refuses (too close to a wall or edge) are still skipped by the red-X check, as before.
* **Click before placing.** Before placing (or recording) units, BMO clicks the middle of the Roblox window once to clear the pop-up that Wizard World shows when a match loads (like Dungeon Train).
