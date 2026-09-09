# Changelog

## 0.5.0 — 2026-09-09

### Added

- **TrimUI Smart Pro port** (`packaging/trimui/`, `scripts/package_trimui.py`):
  - a TrimUI media source that finds the cover art TrimUI ships itself;
  - a TrimUI key map, selectable through an environment variable;
  - extra font candidate directories, so Chinese stops rendering as boxes;
  - the launcher can now be given a RetroArch config path.
- **Button sounds**: move / confirm / back, synthesised through ALSA (no audio
  files in the bundle).  The settings menu gains a "button sound" switch and a
  "button volume" row; both volume rows are **applied as you move them**
  instead of waiting for A.  The volume rocker now moves **both** the preview
  volume and the button volume, keeping the gap between them.
- **Scrolling descriptions**: a blurb that does not fit the bottom panel (or
  the single-screen strip) scrolls sideways instead of being cut off
  mid-sentence.  Only a blurb that actually overflows scrolls.

### Improved

- **Idle cover warm-up**: once a list has stood still for 0.35 s, the whole
  platform is warmed in the background as **one complete set** per game (the
  four carousel sizes, the grid, the list, the detail strip and two logos —
  eight files).  Any input puts the warm-up aside and it resumes where it left
  off.  One source is decoded once for every size: 103 ms for four sizes
  against ~300 ms the old way.
- **Home page warm-up**: the platform picker warms its own artwork too — each
  platform's background and logo (its cache held 32 items, so a 63-platform
  library evicted a card before you scrolled back to it) plus the six preview
  covers.
- **Progress readout**: the game count now reads "531 · 85 cached", counted in
  *games* — a game counts once its whole set is on the card.  All three views
  share one set, so switching views no longer makes the number drop.
- Variant system directories (`FBNEO ACT` and friends) show a variant badge on
  their card.

### Fixed

- **Preview dead after a game**: coming back from a game, clips no longer
  played at all and the UI could crash.  Fixed the ffmpeg environment probe
  (it must cope with the codecs the firmware ships) and the order in which the
  sound card is released and taken back.
- The platform art cache holds 256 entries instead of 32, so scrolling away and
  back no longer re-decodes a card.

---

## 0.4.0 — 2026-09-06

### Added

- **Launch on boot**: new "开机启动 / Autostart" switch in the settings menu
  (START).  When enabled the device boots straight into Retrostation instead of
  the stock menu.
- **System power actions**: the exit dialog now offers three choices —
  **Quit / Reboot / Power off** (Up/Down to pick, A to confirm).  Reboot and
  power off are real system operations.
- **Launch transition**: starting a game now shows a "正在启动《name》" splash
  with a spinner before handing over to the emulator.  Previously the screen
  froze on the last UI frame (or went black) for a couple of seconds, which
  looked like a hang.
- **Search shows the platform**: when searching from the ALL overview, each hit
  gets a second line naming the platform it belongs to, so entries that share a
  title can be told apart.

### Fixed

- **System directories with a variant suffix were unrecognised**: folders such
  as `FBNEO ACT`, `GBA Hank` or `MAME ACT` — a `<system> <variant>` name — are
  absent from the system table, so their cover, logo and launch core all came
  up empty.  They now fall back to the base system named by the first word:
  `FBNEO ACT` → `fbneo`, `GBA Hank` → `gba`, `MAME ACT` → `mame`.  Artwork shows
  and the game launches.
- **Frontend crashed on a read-only user partition**: when the kernel mounts the
  user partition read-only (e.g. `errors=remount-ro`), saving the config and the
  resume state raised `OSError` and took the whole frontend down.  Both are now
  best-effort: a warning is logged and the launch continues (the launch command
  itself goes to `/tmp`, which stays writable).
- **Platforms without artwork rendered as a blank tile**: when a platform ships
  neither a cover nor a logo, its name is now drawn onto the placeholder
  instead of leaving an unidentifiable blank.

### Improved

- List scrolling render performance (fewer redundant repaints per frame).

### Docs

- README: added the APPS-menu installation instructions (Chinese and English).

---

## 0.3.0 and earlier

See `CHANGELOG.md` (Chinese) for the 0.3.0 entry; releases before that were not
recorded.
