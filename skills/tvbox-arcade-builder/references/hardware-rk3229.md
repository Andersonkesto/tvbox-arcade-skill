# Rockchip RK3229 / RK322x Hardware & Emulation Reference

## Typical Board Profile (`R3229Q` / `R329Q` / MXQ-style)
- **CPU:** 4x ARM Cortex-A7 (`armv7l`, NEON/VFPv4), up to `1.2 GHz` (`1200000 kHz`).
- **GPU:** ARM Mali-400 MP (OpenGL ES 2.0).
- **RAM:** 512 MB or 1 GB DDR3 (verify `/proc/meminfo` before tuning).
- **Storage:** Frequently **Raw NAND** (`storagemedia=nand`, `/dev/block/rknand_*`) rather than eMMC (`mmcblk*`).
- **Wi-Fi (common):** SDIO `3030:3030` (SSV6051 family) — Ethernet `eth0` is more reliable for ADB and transfers.

## Why Optimized Android Beats Batocera / Linux on RK3229 Raw NAND
- **Batocera:** No official RK3229/RK322x image exists.
- **Lakka (`ilmich` RK322x) / Armbian (`rk322x` + Multitool):** Community Linux builds exist for SD testing, but RK3229 BootROM prioritizes valid miniloaders on internal NAND. Erasing NAND without a tested Maskrom recovery path (often BGA CLK test-point micro-soldering) risks hard-bricking the board.
- **Android 7.1.2 (SDK 25) + RetroArch `ra32` + Pegasus:** Uses native vendor Mali-400 GLES 2.0, HDMI audio, and network drivers with zero NAND brick risk.

## Verified Emulation Ceilings (1 GB RAM + Cortex-A7 @ 1.2 GHz)

| System | Recommended Core (`armeabi-v7a`) | Expected Performance |
|---|---|---|
| Arcade (CPS1/2/3, Neo Geo, Cave) | `fbneo_libretro_android.so` | Full speed (60 fps) |
| Classic MAME | `mame2003_plus_libretro_android.so` | Full speed (60 fps) for 2D/classic titles |
| SNES | `snes9x2010_libretro_android.so` | Full speed (`snes9x2010` is faster than mainline `snes9x` on A7) |
| Mega Drive / Master System / CD / 32X | `picodrive_libretro_android.so` | Full speed (60 fps) |
| NES / Famicom | `fceumm_libretro_android.so` | Full speed (60 fps) |
| Game Boy / GBC / GBA | `mgba_libretro_android.so` | Full speed (60 fps) |
| PlayStation 1 | `pcsx_rearmed_libretro_android.so` | Good at 1x native resolution (`.chd` / `.pbp` recommended) |
| N64 / Dreamcast / PSP / Saturn / 3D Arcade | — | Too slow on Cortex-A7 + Mali-400 |

## Critical Android 7.1 / Qt 5.15 Compatibility Notes
- **Frontend choice:** Modern Daijisho requires Android 8.0+ (API 26) and fails on Android 7.1 (API 25) with `INSTALL_FAILED_OLDER_SDK`. Use 32-bit **Pegasus Frontend** (`*_android.apk`).
- **Qt 5.15 QML engine:** Pegasus Android builds ship Qt 5.15.10, whose QJSEngine rejects ES2020 nullish coalescing (`??`) and optional chaining (`?.`). Always grep theme `.qml`/`.js` files for `\?\?` and `\?\.` before deploying.
- **Actual `settings.txt` path on Android:** Pegasus reads `settings.txt` and `game_dirs.txt` from `/sdcard/Android/data/org.pegasus_frontend.android/files/pegasus-frontend/`, while themes can reside in `/sdcard/pegasus-frontend/themes/`.
- **USB drive path on Android:** A FAT32 USB drive with volume serial `XXXX-XXXX` mounts at `/storage/XXXX-XXXX/`.
