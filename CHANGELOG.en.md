# Changelog

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
