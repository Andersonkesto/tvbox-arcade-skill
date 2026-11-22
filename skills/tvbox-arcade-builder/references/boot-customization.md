# Boot Screen, Bootanimation & Pegasus Splash Customization

> ⚠️ **Safety first:** Always pull backups of `/system/media/bootanimation.*` and dump `/dev/block/rknand_resource` (`dd if=/dev/block/rknand_resource of=/data/local/resource_backup.img bs=4096`) to safe storage on the host PC **before** writing any changes.

## 1. Boot Video (`/system/media/bootanimation.ts`)
Rockchip Android 7 firmware plays a silent MPEG-TS video before the standard Android boot animation:

```bash
ffmpeg -y -i input.mp4 -an -c:v libx264 -profile:v main -level 4.0 \
  -pix_fmt yuv420p -vf "scale=1280:720" -r 24 -b:v 1800k \
  -maxrate 2200k -bufsize 3600k -f mpegts bootanimation.ts
```

## 2. Hold Loop Animation (`/system/media/bootanimation.zip`)
Create a `desc.txt` (`1280 720 4\np 0 0 hold\n`) and PNG frames inside `hold/`, archived with **`ZIP_STORED` (zero compression)** — Android `bootanimation` rejects deflated ZIPs.

### Critical `/system` Free-Space Rule
Stock RK3229 `/system` partitions often have `< 20 MB` free. Overwriting with `cp` while old large files still occupy blocks can fail mid-write (`cp: xwrite: No space left on device`), leaving a truncated `.ts`:
1. Remount `rw`: `mount -o rw,remount /system`
2. **Delete old files first + `sync`**: `rm -f /system/media/bootanimation.ts /system/media/bootanimation.zip && sync`
3. Copy replacements, set `chmod 644` and `chown root:root`, verify **exact byte count** via `ls -la /system/media/`, then `sync` and `mount -o ro,remount /system`.

## 3. Bootloader / Kernel Logo (`rknand_resource`)
Never write to `rknand_resource` using hardcoded offsets across different boards. Use [patch_boot_resource.py](../scripts/patch_boot_resource.py):
1. Dump current `rknand_resource` from the device and keep an unmodified backup.
2. Run `python scripts/patch_boot_resource.py --resource-in resource_backup.img --image custom.png --resource-out resource_patched.img`.
3. Verify output file size matches `resource_backup.img` byte-for-byte, then flash via `dd if=/data/local/tmp/resource_patched.img of=/dev/block/rknand_resource bs=4096 && sync`.

## 4. Pegasus Loading Screen Logo (`assets/logo.png`)
Pegasus loads its precompiled splash resources from `lib/armeabi-v7a/libpegasus-fe_armeabi-v7a.so` (`assets/logo.png` is stored as an uncompressed Qt resource PNG, `700x227` in `1.0` releases, preceded by a 4-byte big-endian length).
1. Back up `/sdcard/Android/data/org.pegasus_frontend.android/` before proceeding, because replacing the certificate requires uninstalling the original APK.
2. Run `python scripts/patch_pegasus_logo.py --apk Pegasus.apk --image custom_logo.png --out-unsigned Pegasus-unsigned.apk`.
3. Sign `Pegasus-unsigned.apk` with your own keystore (`apksigner` or `SignApk`), install, restore `settings.txt` & `game_dirs.txt`, grant storage permissions, and re-set Home activity if needed.

## 5. Rollback Commands
- **Restore original launcher:**
  `su -c 'cmd package set-home-activity <original_pkg>/<original_activity>'`
- **Re-enable any disabled package:**
  `su -c 'pm enable <package_name>'`
- **Restore `rknand_resource`:**
  `su -c 'dd if=/data/local/resource_backup.img of=/dev/block/rknand_resource bs=4096 && sync'`
