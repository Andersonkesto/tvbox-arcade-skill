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

Helper scripts live in `scripts/` (Python 3.10+, `pip install -r requirements.txt`):

| Script | Purpose | Changes device? |
|---|---|---|
| [audit_tvbox.py](./scripts/audit_tvbox.py) | Hardware/capability audit in one ADB round trip | No |
| [deploy_tvbox.py](./scripts/deploy_tvbox.py) | Install APKs, cores, configs, theme, keylayouts, kiosk settings, debloat, HOME — records a rollback JSON | Only with `--apply` |
| [rollback_tvbox.py](./scripts/rollback_tvbox.py) | Undo everything `deploy_tvbox.py` recorded | Only with `--apply` |
| [verify_tvbox.py](./scripts/verify_tvbox.py) | Automated Verification Checklist (exit code 0/1) | No (unless `--test-rom`) |
| [lint_qml_theme.py](./scripts/lint_qml_theme.py) | Finds ES2020+ syntax that breaks Qt 5.15 Pegasus themes | No (host only) |
| [sync_usb_media.py](./scripts/sync_usb_media.py) | ROM sync, `metadata.pegasus.txt`, libretro-thumbnails artwork | No (host only) |
| [gen_music_qml.py](./scripts/gen_music_qml.py) | RockFFPlayer-safe background music `Music.qml` | No (host only) |
| [build_console_cards.py](./scripts/build_console_cards.py) | Uniform 600x600 console cards | No (host only) |
| [patch_boot_resource.py](./scripts/patch_boot_resource.py) | Patch boot logos inside a dumped `resource` image | No (host only) |
| [patch_pegasus_logo.py](./scripts/patch_pegasus_logo.py) | Patch Pegasus loading logo, optional align + sign | No (host only) |

## Mandatory Safety Guardrails

Before modifying any device state, ALWAYS enforce these rules:

1. **Read-only hardware audit first:** Verify SoC, ABI (`ro.product.cpu.abi`), Android SDK (`ro.build.version.sdk`), RAM (`/proc/meminfo`), storage type (`storagemedia` / `/proc/partitions`), and free space (`df /system /data`).
2. **Never flash Linux/Batocera blindly onto Raw NAND (`rknand_*`):** On RK3229 Raw NAND boards, BootROM prioritizes valid NAND miniloaders over SD cards; erasing NAND without verified Maskrom recovery risks bricking BGA boards. Prefer the optimized Android + RetroArch 32-bit + Pegasus path unless the user explicitly chooses SD-only Linux testing with full NAND backup.
3. **Explicit user confirmation before destructive or privileged system writes:** Ask before remounting `/system` as `rw`, disabling packages (`pm disable-user`), changing the default Home launcher (`cmd package set-home-activity`), or writing block devices (`dd` to `rknand_resource`). Run `deploy_tvbox.py` **without** `--apply` first, show the user the dry-run plan, and only add `--apply` after they confirm.
4. **Never write `dd` with hardcoded offsets:** Always dump the device's own `resource` partition first, keep a verified backup copy, and use [patch_boot_resource.py](./scripts/patch_boot_resource.py) to locate logos through the `RSCE` index table (falls back to validated `BM` scanning) with exact BMP size checks.
5. **Check `/system` free space before copying media:** Delete old `/system/media/bootanimation.*` files before copying replacements if free space is tight (< 20 MB — the audit warns automatically), and verify exact byte sizes after copy before remounting `/system` read-only.
6. **Always preserve rollback:** Keep the `tvbox_rollback.json` + `tvbox_backup/` written by `deploy_tvbox.py` (original Home launcher, previous settings, disabled packages, replaced files). Never uninstall core Android input, storage, audio, display, settings, or ADB components — `deploy_tvbox.py` refuses protected packages.

---

## Workflow

### Phase 1 — Hardware & Capability Audit

Run `python scripts/audit_tvbox.py --serial <IP>:5555 --json` (after `adb connect <IP>:5555`). It exits with code 2 instead of printing an empty report when no device is reachable, and captures in a single `adb shell` call:
- `uname -a`, ABI, SDK, board/platform, `/proc/meminfo`, `/proc/partitions`, `/proc/cmdline`, free MB on `/system` and `/data`
- GLES renderer (`dumpsys SurfaceFlinger`), display size (`wm size`), SoC temperatures, root availability (`su -c id`)
- External FAT/exFAT volumes under `/storage/XXXX-XXXX`, installed RetroArch/Pegasus, current HOME, USB encoders/gamepads

Consult [hardware-rk3229.md](./references/hardware-rk3229.md) for verified emulation ceilings (2D Arcade/8-16 bit/GBA/GBC/PS1 run great; N64/Dreamcast/PSP/3D arcade do not).

### Phase 2 — Emulator & Frontend Installation

1. **Match ABI and Android SDK:**
   - For 32-bit `armeabi-v7a` Android 7.1 (API 25):
     - Download official `RetroArch_ra32.apk` from `https://buildbot.libretro.com/stable/` and `armeabi-v7a` cores (`fbneo`, `mame2003_plus`, `snes9x2010`, `picodrive`, `fceumm`, `mgba`, `pcsx_rearmed`) from `https://buildbot.libretro.com/nightly/android/latest/armeabi-v7a/`.
     - Download official 32-bit **Pegasus Frontend** (`*_android.apk`) from `https://github.com/mmatyas/pegasus-frontend/releases` (note: modern Daijisho requires API 26+ and fails with `INSTALL_FAILED_OLDER_SDK` on Android 7.1).
2. **Deploy with [deploy_tvbox.py](./scripts/deploy_tvbox.py)** (dry-run first, then `--apply` after confirmation):
   ```bash
   python scripts/deploy_tvbox.py --serial <IP>:5555 --su \
     --retroarch-apk RetroArch_ra32.apk --pegasus-apk pegasus-fe_android.apk \
     --cores-dir ./cores --configs --theme-dir ./RGLegacy --game-dirs ./game_dirs.txt
   ```
   - **Cores** are copied into `/data/data/com.retroarch.ra32/cores/` with RetroArch's UID, `chmod 755` and `restorecon` — this needs **root** (`--su`). **Without root**, skip `--cores-dir` and install cores inside RetroArch: *Main Menu → Online Updater → Core Downloader* (temporarily disable kiosk mode first), then also run *Update Core Info Files* so `libretro_info_path` is populated.
   - **`--configs`** pushes (backing up whatever it replaces):
     - [retroarch.cfg](./resources/retroarch.cfg) → `/sdcard/Android/data/com.retroarch.ra32/files/retroarch.cfg` (`gl` + threaded video, 16:9, `rgui` menu, 48 kHz/low-cost resampler, `config_save_on_exit = "false"` so players cannot persist menu changes, **kiosk mode on** — set `kiosk_mode_password` before deploying a public cabinet; unlock via *Main Menu → Disable Kiosk Mode*).
     - [retroarch-core-options.cfg](./resources/retroarch-core-options.cfg) → same folder (`global_core_options = "true"`): MAME 2003-Plus skips disclaimer/warnings, lighter FBNeo audio, PCSX ReARMed dynarec on / NEON enhancement off, PicoDrive dynarec. Confirm values in *Quick Menu → Core Options* after the first launch of each core — keys can change between core versions.
     - [resources/autoconfig/](./resources/autoconfig/) → `/sdcard/RetroArch/autoconfig/android/` (RetroArch matches by `input_device` inside the file, not by filename).
     - [pegasus_settings.txt](./resources/pegasus_settings.txt) → `/sdcard/Android/data/org.pegasus_frontend.android/files/pegasus-frontend/settings.txt` (**not** `/sdcard/pegasus-frontend/settings.txt`). Disables the Android-apps, Steam, GOG, ES2, LaunchBox, Logiqx, Lutris, Playnite and Skraper providers so the USB scan is faster. Use `--locale en` (etc.) to override `general.locale: pt-BR`.
3. **Install Qt 5.15-compatible Frontend Theme (`RGLegacy`):**
   - **Critical pitfall:** Pegasus Android builds bundling Qt 5.15.10 reject ES2020/ES2021 operators (`??`, `?.`, `??=`, `||=`, `&&=`). Run `python scripts/lint_qml_theme.py <theme_dir>` on any theme (exit code 1 = incompatible); `deploy_tvbox.py --theme-dir` runs this lint automatically and refuses to push a failing theme.
   - Use **RGLegacy** (`https://github.com/ZagonAb/RGLegacy/archive/refs/heads/main.zip`) — 100% Qt 5.15-safe, console carousel + per-system colors + A-Z alpha jump + media preview. **Important:** rename the extracted `RGLegacy-main/` folder to `RGLegacy/` before pushing to `/sdcard/pegasus-frontend/themes/RGLegacy/` (the deployer rejects `*-main` folders), otherwise Pegasus falls back to its default theme.
   - Copy `snkneogeo.png` -> `neogeo.png`, add `"neogeo"` to `ConsoleColors.qml` and `ConsoleYears.qml`, hide the mobile battery widget in `TopBar.qml` (`visible: false`, `batteryTimer.running: false`), and prioritize `boxFront` (priority 1) + `screenshot` (priority 2) in `GameListView.qml`.
   - Optionally build uniform `600x600` console hardware + logo cards using [build_console_cards.py](./scripts/build_console_cards.py).

### Phase 3 — External USB/SD ROMs, Artwork & Background Music

1. **Prefer FAT32/exFAT USB/SD storage** when internal `/data` has < 5 GB free (the audit warns). Leave at least 1.5 GB free on internal NAND.
2. Identify the USB drive volume serial (`external_volumes` in the audit, or `vol <DRIVE>:` on Windows -> `/storage/<SERIAL>/` on Android) and list `/storage/<SERIAL>/ROMs/<system>` in `game_dirs.txt`.
3. Organize ROMs and fetch official artwork (`boxfront.png`, `screenshot.png`) with [sync_usb_media.py](./scripts/sync_usb_media.py) (`--libretro-repo "FBNeo - Arcade Games"` etc. + optional MAME/FBNeo XML DATs to map short ZIP names to full titles; excludes BIOS ZIPs like `neogeo.zip` from game lists).
   - It downloads each libretro-thumbnails repo's file list **once** (GitHub tree API) and matches titles locally (exact, then region/tag-insensitive), retrying with backoff on rate limits. Export `GITHUB_TOKEN` for a higher API limit; `--no-index` restores blind URL guessing.
   - For collections that already ship Batocera/ES-style `images/*-thumb.png` & `*-image.png`, pass `--es-images` to generate `metadata.pegasus.txt` referencing `assets.boxFront` and `assets.screenshot` directly without duplicating files (and automatically emitting `ignore-file:` entries for BIOS ZIPs).
   - Copy arcade audio `samples/*.zip` to `/sdcard/RetroArch/system/fbneo/samples/` and BIOS files (`neogeo.zip`, `scph5501.bin`) to `/sdcard/RetroArch/system/`.
4. **Background Music (`.ogg`/`.mp3`) on Rockchip `RockFFPlayer`:**
   - **Critical pitfall:** Do NOT put dozens/hundreds of `PlaylistItem` entries inside QtMultimedia `Playlist` — `RockFFPlayer` rapidly opens/destroys all streams and stops silently.
   - Generate a lightweight single-track-at-a-time `Music.qml` using [gen_music_qml.py](./scripts/gen_music_qml.py). It plays a shuffled full-deck queue without repeats, loops infinitely, pauses on app background (`Qt.application.state`), includes a 2-second watchdog to recover if `RockFFPlayer` enters `StoppedState` without firing `EndOfMedia`, and backs off to one retry every 30 s when every track fails (e.g. USB drive missing).

### Phase 4 — Dedicated Kiosk, Debloat, Controllers & Coin Acceptor

1. **Android kiosk settings (no root):** `deploy_tvbox.py --android-kiosk` sets window/transition/animator scales to `0`, disables screensaver and screen-off sleep, and records previous values for rollback.
2. **Safe Arcade Debloat (after user confirmation):** `deploy_tvbox.py --disable-packages pkg1,pkg2` runs `pm disable-user --user 0` only on non-essential apps (media center bloat, stress tests, calculator, unused launchers, Bluetooth if unused, TV streaming apps). It refuses `systemui`, `settings`, `tv.settings`, `inputdevices`, `externalstorage`, `providers.media`, `shell`, input methods, WebView, RetroArch and Pegasus.
3. **Controllers & Coin Acceptor:** Consult [controllers-and-coin.md](./references/controllers-and-coin.md). `deploy_tvbox.py --keylayouts --su` installs the `.kl` files from [resources/keylayouts/](./resources/keylayouts/) into `/system/usr/keylayout/` (backing up any existing file):
   - **Zero Delay Arcade (`0079:0006`):** `Vendor_0079_Product_0006.kl` + `DragonRise_ZeroDelay.cfg`. **Hotkeys use a hidden service button on K11** (not `Select`), because the coin acceptor pulses `Select`: with `Select` as hotkey, a coin dropped while a player holds Start would quit the game. Wire a small service button inside the cabinet to K11.
   - **Dual-Mode PS3/PC (`054c:0268` / `0e8f:0003`):** `Vendor_0e8f_Product_0003.kl` + `MyPower_PS3_PC.cfg` (`Select + Start` exit is fine here — no coin acceptor). Linux 3.10 `hid-sony` can stall clones in `054c:0268` mode; hold Mode/PS to switch to `0e8f:0003`.
4. **Set Pegasus as HOME:** `deploy_tvbox.py --set-home` records the original launcher first.

### Phase 5 — Optional Boot & Splash Branding

Consult [boot-customization.md](./references/boot-customization.md) for:
- Rockchip `bootanimation.ts` (H.264 Main, `yuv420p`, `1280x720`, `24fps`, `-an`, `mpegts`) and `bootanimation.zip` (`ZIP_STORED` hold loop).
- Boot logo patching inside a backed-up `rknand_resource` image via [patch_boot_resource.py](./scripts/patch_boot_resource.py): `--list` shows RSCE entries, `--extract DIR` saves the current logos as PNG, and patching updates **both** `logo.bmp` (U-Boot) and `logo_kernel.bmp` (kernel) by default, supports 24-bit and 8-bit palettized BMPs, keeps aspect ratio (`--fit contain`) and prints SHA-256 of input/output.
- Optional Pegasus loading screen logo replacement (`assets/logo.png` inside `libpegasus-fe_armeabi-v7a.so`) via [patch_pegasus_logo.py](./scripts/patch_pegasus_logo.py) — pass `--keystore my.jks --ks-pass env:KS_PASS` to also run `zipalign -p 4` + `apksigner sign/verify`. Re-signing changes the APK signature: back up `Android/data/org.pegasus_frontend.android/files/pegasus-frontend/` before uninstall/reinstall.

### Phase 6 — Lockdown & Rollback

- **Public cabinets:** after verification, turn off network ADB (Developer options → disable ADB / network debugging, or `adb usb`). Anyone on the same network can otherwise get a shell on port 5555.
- **Rollback:** `python scripts/rollback_tvbox.py --rollback-file tvbox_rollback.json [--su]` (dry-run) then `--apply` restores the original HOME, re-enables packages, restores settings, keylayouts and replaced config files. APKs, cores, themes and ROM media are left in place.

---

## Verification Checklist

Never declare setup complete until every applicable check passes on the live device. Run `python scripts/verify_tvbox.py --serial <IP>:5555 --expect-home --expect-bgm --screenshot screen.png` (exit code 0 = all passed), which automates:

- `lastrun.log` (`/sdcard/Android/data/org.pegasus_frontend.android/files/pegasus-frontend/lastrun.log`) -> target theme loaded, game count, **zero** QML `Syntax error` / `ReferenceError` / `TypeError`.
- `dumpsys window windows | grep mCurrentFocus` -> `org.pegasus_frontend.android/.MainActivity` is in foreground.
- `cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN` -> Pegasus is default Home (if requested).
- `dumpsys media.audio_flinger` -> `Standby: no` and at least 1 active track when BGM is enabled.
- `screencap -p` pulled -> **visually inspect** console cards, game covers, clean TopBar (no battery icon), and custom splash (if patched).
- Optional `--test-rom /storage/<SERIAL>/ROMs/arcade/<game>.zip --test-core fbneo_libretro_android.so` -> RetroArch opens in foreground without crashing (then it is force-stopped).
- Physically test the cabinet: coin pulse adds 1 credit without quitting, service button (K11) + Start exits to Pegasus.
