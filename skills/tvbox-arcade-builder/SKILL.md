---
name: tvbox-arcade-builder
license: MIT
description: >-
  Use when turning an Android TV Box (Rockchip RK3229/RK322x, Android 7.1,
  armeabi-v7a, Mali-400, 1 GB RAM, Raw NAND) into a dedicated retro arcade
  console via ADB, or when asked in Portuguese to transformar TV Box em
  arcade/fliperama com RetroArch, Pegasus Frontend, capas no pendrive USB,
  controles Zero Delay, moedeiro ou tela de boot personalizada.
---

# TV Box Arcade Builder

## Overview

End-to-end engineering runbook and helper toolset for converting low-end Android TV Boxes (such as Rockchip RK3229/RK322x boards) into dedicated, polished retro arcade systems via ADB — maximizing stability and performance while avoiding Raw NAND brick risks.

## Mandatory Safety Guardrails

Before modifying any device state, ALWAYS enforce these rules:

1. **Read-only hardware audit first:** Verify SoC, ABI (`ro.product.cpu.abi`), Android SDK (`ro.build.version.sdk`), RAM (`/proc/meminfo`), storage type (`storagemedia` / `/proc/partitions`), and free space (`df -h /system /data`).
2. **Never flash Linux/Batocera blindly onto Raw NAND (`rknand_*`):** On RK3229 Raw NAND boards, BootROM prioritizes valid NAND miniloaders over SD cards; erasing NAND without verified Maskrom recovery risks bricking BGA boards. Prefer the optimized Android + RetroArch 32-bit + Pegasus path unless the user explicitly chooses SD-only Linux testing with full NAND backup.
3. **Explicit user confirmation before destructive or privileged system writes:** Ask before remounting `/system` as `rw`, disabling packages (`pm disable-user`), changing the default Home launcher (`cmd package set-home-activity`), or writing block devices (`dd` to `rknand_resource`).
4. **Never write `dd` with hardcoded offsets:** Always dump the device's own `resource` partition first, keep a verified backup copy, and use [patch_boot_resource.py](./scripts/patch_boot_resource.py) to dynamically locate and validate the `RSCE` header + `BM` signature + exact BMP size.
5. **Check `/system` free space before copying media:** Delete old `/system/media/bootanimation.*` files before copying replacements if free space is tight (< 20 MB), and verify exact byte sizes after copy before remounting `/system` read-only.
6. **Always preserve rollback:** Record the original Home launcher package and never uninstall core Android input, storage, audio, display, settings, or ADB components.

---

## Workflow

### Phase 1 — Hardware & Capability Audit

Run the read-only ADB audit helper (`python scripts/audit_tvbox.py --serial <IP>:5555 --json`) or run the ADB checks manually (`adb connect <IP>:5555`) to capture:
- `uname -a`, `getprop ro.product.cpu.abi`, `getprop ro.build.version.sdk`
- `/proc/cpuinfo`, `/proc/meminfo`, `df -h`, `/proc/partitions`, `/proc/cmdline`
- `dumpsys SurfaceFlinger` (GLES version / GPU) and `/proc/bus/input/devices` (USB encoders/gamepads)
- `cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN`

Consult [hardware-rk3229.md](./references/hardware-rk3229.md) for verified emulation ceilings (2D Arcade/8-16 bit/GBA/GBC/PS1 run great; N64/Dreamcast/PSP/3D arcade do not).

### Phase 2 — Emulator & Frontend Installation

1. **Match ABI and Android SDK:**
   - For 32-bit `armeabi-v7a` Android 7.1 (API 25):
     - Download official `RetroArch_ra32.apk` from `https://buildbot.libretro.com/stable/` and `armeabi-v7a` cores (`fbneo`, `mame2003_plus`, `snes9x2010`, `picodrive`, `fceumm`, `mgba`, `pcsx_rearmed`) from `https://buildbot.libretro.com/nightly/android/latest/armeabi-v7a/`.
     - Download official 32-bit **Pegasus Frontend** (`*_android.apk`) from `https://github.com/mmatyas/pegasus-frontend/releases` (note: modern Daijisho requires API 26+ and fails with `INSTALL_FAILED_OLDER_SDK` on Android 7.1).
2. **Deploy RetroArch config & cores:**
   - Install APKs with `adb install -r -g`.
   - Copy `.so` cores into `/data/data/com.retroarch.ra32/cores/` with RetroArch's UID (`dumpsys package com.retroarch.ra32 | grep userId`) and `chmod 755`.
   - Push [retroarch.cfg](./resources/retroarch.cfg) to `/sdcard/Android/data/com.retroarch.ra32/files/retroarch.cfg` (sets `gl`, `video_threaded = "true"`, 16:9 fullscreen `aspect_ratio_index = "1"`, `input_overlay_enable = "false"`, and `joypad_autoconfig_dir = "/storage/emulated/0/RetroArch/autoconfig"`).
3. **Install Qt 5.15-compatible Frontend Theme (`RGLegacy`):**
   - **Critical pitfall:** Pegasus Android builds bundling Qt 5.15.10 reject ES2020 `??` and `?.` operators. Audit QML themes before deploying.
   - Use **RGLegacy** (`https://github.com/ZagonAb/RGLegacy/archive/refs/heads/main.zip`) — 100% Qt 5.15-safe, console carousel + per-system colors + A-Z alpha jump + media preview. **Important:** rename the extracted `RGLegacy-main/` folder to `RGLegacy/` before pushing to `/sdcard/pegasus-frontend/themes/RGLegacy/`, otherwise Pegasus falls back to its default theme.
   - Copy `snkneogeo.png` -> `neogeo.png`, add `"neogeo"` to `ConsoleColors.qml` and `ConsoleYears.qml`, hide the mobile battery widget in `TopBar.qml` (`visible: false`, `batteryTimer.running: false`), and prioritize `boxFront` (priority 1) + `screenshot` (priority 2) in `GameListView.qml`.
   - Optionally build uniform `600x600` console hardware + logo cards using [build_console_cards.py](./scripts/build_console_cards.py).
   - **Config location pitfall:** Pegasus on Android reads `settings.txt` from `/sdcard/Android/data/org.pegasus_frontend.android/files/pegasus-frontend/settings.txt` (NOT `/sdcard/pegasus-frontend/settings.txt`). Push [pegasus_settings.txt](./resources/pegasus_settings.txt) to the app's `Android/data` path (disabling `providers.androidapps.enabled: false`; adjust `general.locale` from `pt-BR` if targeting another language).

### Phase 3 — External USB/SD ROMs, Artwork & Background Music

1. **Prefer FAT32/exFAT USB/SD storage** when internal `/data` has < 5 GB free. Leave at least 1.5 GB free on internal NAND.
2. Identify the USB drive volume serial (`vol <DRIVE>:` on Windows -> `/storage/<SERIAL>/` on Android) and list `/storage/<SERIAL>/ROMs/<system>` in `game_dirs.txt`.
3. Organize ROMs and fetch official artwork (`boxfront.png`, `screenshot.png`) with [sync_usb_media.py](./scripts/sync_usb_media.py) (uses `raw.githubusercontent.com/libretro-thumbnails/...` + optional MAME/FBNeo XML DATs to map short ZIP names to full game titles; excludes BIOS ZIPs like `neogeo.zip` from game lists).
   - For collections that already ship Batocera/ES-style `images/*-thumb.png` & `*-image.png`, pass `--es-images` to [sync_usb_media.py](./scripts/sync_usb_media.py) to generate `metadata.pegasus.txt` referencing `assets.boxFront` and `assets.screenshot` directly without duplicating files (and automatically emitting `ignore-file:` entries for BIOS ZIPs).
   - Copy arcade audio `samples/*.zip` to `/sdcard/RetroArch/system/fbneo/samples/` and BIOS files (`neogeo.zip`, `scph5501.bin`) to `/sdcard/RetroArch/system/`.
4. **Background Music (`.ogg`/`.mp3`) on Rockchip `RockFFPlayer`:**
   - **Critical pitfall:** Do NOT put dozens/hundreds of `PlaylistItem` entries inside QtMultimedia `Playlist` — `RockFFPlayer` rapidly opens/destroys all streams and stops silently.
   - Generate a lightweight single-track-at-a-time `Music.qml` using [gen_music_qml.py](./scripts/gen_music_qml.py). It plays a shuffled full-deck queue without repeats, loops infinitely, pauses on app background (`Qt.application.state`), and includes a 2-second watchdog timer to recover if `RockFFPlayer` enters `StoppedState` without firing `EndOfMedia`.

### Phase 4 — Dedicated Debloat, Controllers & Coin Acceptor

1. **Safe Arcade Debloat (after user confirmation):** Disable only non-essential apps (`pm disable-user --user 0`) such as media center bloat, stress tests, calculator, unused launchers, Bluetooth (if unused), and TV streaming apps. Never disable `systemui`, `settings`, `tv.settings`, `inputdevices`, `externalstorage`, `providers.media`, or `shell`.
2. **Controllers & Coin Acceptor:** Consult [controllers-and-coin.md](./references/controllers-and-coin.md) and deploy the appropriate Android `.kl` from [resources/keylayouts/](./resources/keylayouts/) and RetroArch `.cfg` from [resources/autoconfig/](./resources/autoconfig/):
   - **Zero Delay Arcade (`0079:0006`):** `Vendor_0079_Product_0006.kl` + `DragonRise_ZeroDelay.cfg` (6-button fighting layout + `Select+Start` exit; electronic coin acceptor NO/GND wired to `Select` adds coins natively in MAME/FBNeo — never connect 12V to the USB encoder).
   - **Dual-Mode PS3/PC (`054c:0268` / `0e8f:0003`):** `Vendor_0e8f_Product_0003.kl` + `MyPower_PS3_PC.cfg` (note: Linux 3.10 `hid-sony` can stall clones in `054c:0268` mode; hold Mode/PS to switch to `0e8f:0003`).

### Phase 5 — Optional Boot & Splash Branding

Consult [boot-customization.md](./references/boot-customization.md) for:
- Rockchip `bootanimation.ts` (H.264 Main, `yuv420p`, `1280x720`, `24fps`, `-an`, `mpegts`) and `bootanimation.zip` (`ZIP_STORED` hold loop).
- Dynamic `logo.bmp` patching inside a backed-up `rknand_resource` image via [patch_boot_resource.py](./scripts/patch_boot_resource.py).
- Optional Pegasus loading screen logo replacement (`assets/logo.png` inside `libpegasus-fe_armeabi-v7a.so`) via [patch_pegasus_logo.py](./scripts/patch_pegasus_logo.py), noting that re-signing (after `zipalign -p 4`) changes the APK signature and requires backing up `Android/data/org.pegasus_frontend.android/files/pegasus-frontend/` before uninstall/reinstall.

---

## Verification Checklist

Never declare setup complete until every applicable check passes on the live device:

- `tail -n 30 /sdcard/Android/data/org.pegasus_frontend.android/files/pegasus-frontend/lastrun.log` -> confirms target theme loaded, expected game count found, **zero** QML `Syntax error` / `ReferenceError` / `TypeError`.
- `dumpsys window windows | grep mCurrentFocus` -> confirms `org.pegasus_frontend.android/.MainActivity` is in foreground.
- `cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN` -> confirms Pegasus is default Home (if requested).
- `dumpsys media.audio_flinger` -> confirms `Standby: no` and `1 Tracks of which 1 are active` when BGM is enabled.
- `screencap -p` pulled and visually inspected -> confirms console cards, game covers, clean TopBar (no battery icon), and custom splash (if patched).
- Test launching one ROM via `am start -n com.retroarch.ra32/com.retroarch.browser.retroactivity.RetroActivityFuture ...` -> confirms RetroArch opens in foreground without crashing.
