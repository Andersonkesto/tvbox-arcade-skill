"""Deploy the arcade stack to a TV Box over ADB, recording everything needed to roll back.

DRY-RUN BY DEFAULT: without --apply it only prints the plan (read-only queries
still run so the plan shows real values). Every change made with --apply is
recorded in a rollback JSON (original HOME launcher, previous Android settings,
disabled packages, backups of overwritten files) consumed by rollback_tvbox.py.
Re-running keeps the FIRST recorded originals, so rollback always returns to the
pre-arcade state.

Steps (each opt-in): install APKs -> cores (root) -> configs -> theme (linted)
-> game_dirs.txt -> keylayouts (root) -> Android kiosk settings -> debloat -> HOME.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

from lint_qml_theme import iter_theme_files, lint_source
from tvbox_adb import Adb, AdbError, ensure_device

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCES = os.path.join(SKILL_DIR, "resources")

RA_PKG = "com.retroarch.ra32"
PEGASUS_PKG = "org.pegasus_frontend.android"
PEGASUS_HOME = f"{PEGASUS_PKG}/.MainActivity"
RA_FILES = f"/sdcard/Android/data/{RA_PKG}/files"
PEGASUS_CFG = f"/sdcard/Android/data/{PEGASUS_PKG}/files/pegasus-frontend"
THEMES_DIR = "/sdcard/pegasus-frontend/themes"
AUTOCONFIG_DIR = "/sdcard/RetroArch/autoconfig/android"
KEYLAYOUT_DIR = "/system/usr/keylayout"
DEVICE_TMP = "/data/local/tmp"
DEVICE_BACKUP = f"{DEVICE_TMP}/tvbox_backup"

KIOSK_SETTINGS = [
    ("global", "window_animation_scale", "0"),
    ("global", "transition_animation_scale", "0"),
    ("global", "animator_duration_scale", "0"),
    ("global", "stay_on_while_plugged_in", "7"),
    ("system", "screen_off_timeout", "2147483647"),
    ("secure", "screensaver_enabled", "0"),
    ("secure", "screensaver_activate_on_sleep", "0"),
    ("secure", "screensaver_activate_on_dock", "0"),
]

# Guardrail: never disable core input/storage/display/settings/ADB components or the arcade apps.
PROTECTED_SUBSTRINGS = (
    "systemui", "com.android.settings", "tv.settings", "inputdevices", "externalstorage",
    "providers.media", "providers.settings", "com.android.shell", "packageinstaller",
    "inputmethod", "com.android.phone", "webview", RA_PKG, PEGASUS_PKG,
)


def is_protected(pkg: str) -> bool:
    return pkg in ("android", "com.android.settings") or any(s in pkg for s in PROTECTED_SUBSTRINGS)


class Rollback:
    def __init__(self, path: str, serial: str):
        self.path = path
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {"version": 1, "serial": serial, "created": datetime.datetime.now().isoformat(timespec="seconds"),
                         "home": None, "settings": [], "disabled_packages": [], "files": [], "system_files": []}

    def save(self, apply: bool) -> None:
        if not apply:
            return
        self.data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def _has(self, key: str, field: str, value: str) -> bool:
        return any(item.get(field) == value for item in self.data[key])

    def add_setting(self, ns: str, key: str, previous: str) -> None:
        if not any(s["namespace"] == ns and s["key"] == key for s in self.data["settings"]):
            self.data["settings"].append({"namespace": ns, "key": key, "previous": previous})

    def add_file(self, kind: str, remote: str, backup: str | None) -> None:
        if not self._has(kind, "remote", remote):
            self.data[kind].append({"remote": remote, "backup": backup})

    def add_disabled(self, pkg: str) -> None:
        if pkg not in self.data["disabled_packages"]:
            self.data["disabled_packages"].append(pkg)

    def set_home(self, original: str) -> None:
        if self.data["home"] is None and original and PEGASUS_PKG not in original:
            self.data["home"] = {"original": original}


def safe_name(remote: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", remote.strip("/"))


class Deployer:
    def __init__(self, adb: Adb, rb: Rollback, backup_dir: str):
        self.adb = adb
        self.rb = rb
        self.backup_dir = backup_dir

    def section(self, title: str) -> None:
        print(f"\n== {title}")

    def push_config(self, local: str, remote: str) -> None:
        """Push a file to user storage, backing up whatever it replaces."""
        if self.adb.apply and not self.rb._has("files", "remote", remote):
            os.makedirs(self.backup_dir, exist_ok=True)
            backup = os.path.join(self.backup_dir, safe_name(remote))
            self.rb.add_file("files", remote, backup if self.adb.pull(remote, backup) else None)
        self.adb.shell(f"mkdir -p '{os.path.dirname(remote)}'")
        self.adb.push(local, remote)
        self.rb.save(self.adb.apply)

    def install_apks(self, apks: list[str]) -> None:
        self.section("Install APKs")
        for apk in apks:
            if not os.path.isfile(apk):
                raise AdbError(f"APK not found: {apk}")
            self.adb.install(apk)

    def cores(self, cores_dir: str) -> None:
        self.section("RetroArch cores (root)")
        sos = sorted(glob.glob(os.path.join(cores_dir, "*.so")))
        if not sos:
            raise AdbError(f"No .so cores in {cores_dir}")
        if not self.adb.use_su:
            raise AdbError("Copying cores into /data/data needs root (--su). Without root, install cores "
                           "from RetroArch: Main Menu -> Online Updater -> Core Downloader.")
        m = re.search(r"userId=(\d+)", self.adb.query(f"dumpsys package {RA_PKG}"))
        uid = m.group(1) if m else "<retroarch-uid>"
        if not m and self.adb.apply:
            raise AdbError(f"{RA_PKG} not installed (no userId); install RetroArch first")
        tmp = f"{DEVICE_TMP}/tvbox_cores"
        self.adb.shell(f"mkdir -p {tmp}")
        for so in sos:
            self.adb.push(so, f"{tmp}/{os.path.basename(so)}")
        dst = f"/data/data/{RA_PKG}/cores"
        self.adb.shell(
            f"mkdir -p {dst} && cp {tmp}/*.so {dst}/ && chown -R {uid}:{uid} {dst} && "
            f"chmod 755 {dst} {dst}/*.so && (restorecon -R {dst} 2>/dev/null; true) && rm -rf {tmp}",
            privileged=True,
        )

    def configs(self, locale: str) -> None:
        self.section("RetroArch + Pegasus configs")
        self.push_config(os.path.join(RESOURCES, "retroarch.cfg"), f"{RA_FILES}/retroarch.cfg")
        self.push_config(os.path.join(RESOURCES, "retroarch-core-options.cfg"), f"{RA_FILES}/retroarch-core-options.cfg")
        for cfg in sorted(glob.glob(os.path.join(RESOURCES, "autoconfig", "*.cfg"))):
            self.push_config(cfg, f"{AUTOCONFIG_DIR}/{os.path.basename(cfg)}")
        settings_src = os.path.join(RESOURCES, "pegasus_settings.txt")
        if locale:
            with open(settings_src, encoding="utf-8") as f:
                text = re.sub(r"^general\.locale:.*$", f"general.locale: {locale}", f.read(), flags=re.M)
            os.makedirs(self.backup_dir, exist_ok=True)
            settings_src = os.path.join(self.backup_dir, "pegasus_settings.generated.txt")
            with open(settings_src, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        self.push_config(settings_src, f"{PEGASUS_CFG}/settings.txt")

    def theme(self, theme_dir: str, force: bool) -> None:
        self.section("Pegasus theme (Qt 5.15 lint first)")
        theme_dir = os.path.normpath(theme_dir)
        issues = 0
        for f in iter_theme_files(theme_dir):
            with open(f, encoding="utf-8", errors="replace") as fh:
                for it in lint_source(fh.read()):
                    issues += 1
                    print(f"  LINT {f}:{it['line']}: `{it['operator']}` {it['message']}")
        if issues and not force:
            raise AdbError(f"{issues} Qt 5.15-incompatible construct(s) in theme; fix them or pass --force-theme")
        name = os.path.basename(theme_dir)
        if name.endswith("-main"):
            raise AdbError(f"Rename '{name}' to '{name[:-5]}' first: Pegasus falls back to the default theme otherwise")
        self.adb.shell(f"mkdir -p {THEMES_DIR}")
        self.adb.push(theme_dir, f"{THEMES_DIR}/")

    def game_dirs(self, path: str) -> None:
        self.section("Pegasus game_dirs.txt")
        self.push_config(path, f"{PEGASUS_CFG}/game_dirs.txt")

    def keylayouts(self) -> None:
        self.section("Android keylayouts in /system (root)")
        if not self.adb.use_su:
            raise AdbError("Writing /system/usr/keylayout needs root (--su)")
        for kl in sorted(glob.glob(os.path.join(RESOURCES, "keylayouts", "*.kl"))):
            name = os.path.basename(kl)
            target = f"{KEYLAYOUT_DIR}/{name}"
            existed = bool(self.adb.query(f"ls {target} 2>/dev/null"))
            backup = f"{DEVICE_BACKUP}/{name}" if existed else None
            self.adb.push(kl, f"{DEVICE_TMP}/{name}")
            save_old = f"mkdir -p {DEVICE_BACKUP} && cp -n {target} {backup} && " if existed else ""
            # Always remount /system read-only, but keep the copy's exit status.
            self.adb.shell(
                f"mount -o rw,remount /system && {save_old}cp {DEVICE_TMP}/{name} {target} && "
                f"chmod 644 {target} && chown root:root {target} && rm {DEVICE_TMP}/{name}; "
                "rc=$?; sync; mount -o ro,remount /system; exit $rc",
                privileged=True,
            )
            self.rb.add_file("system_files", target, backup)
            self.rb.save(self.adb.apply)

    def android_kiosk(self) -> None:
        self.section("Android kiosk settings (animations off, no screensaver/sleep)")
        for ns, key, value in KIOSK_SETTINGS:
            previous = self.adb.query(f"settings get {ns} {key}")
            print(f"  {ns}/{key}: {previous!r} -> {value!r}")
            self.adb.shell(f"settings put {ns} {key} {value}")
            self.rb.add_setting(ns, key, previous)
            self.rb.save(self.adb.apply)

    def debloat(self, packages: list[str]) -> None:
        self.section("Disable non-essential packages (pm disable-user)")
        installed = set(self.adb.query("pm list packages").replace("package:", "").split())
        for pkg in packages:
            if is_protected(pkg):
                print(f"  REFUSED {pkg}: protected core/arcade component")
                continue
            if installed and pkg not in installed:
                print(f"  SKIP {pkg}: not installed")
                continue
            self.adb.shell(f"pm disable-user --user 0 {pkg}")
            self.rb.add_disabled(pkg)
            self.rb.save(self.adb.apply)

    def set_home(self) -> None:
        self.section("Default HOME -> Pegasus")
        out = self.adb.query(
            "cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN")
        original = out.splitlines()[-1].strip() if out else ""
        print(f"  current HOME: {original or 'unknown'}")
        self.rb.set_home(original)
        self.rb.save(self.adb.apply)
        self.adb.shell(f"cmd package set-home-activity {PEGASUS_HOME}", privileged=self.adb.use_su)


def main():
    parser = argparse.ArgumentParser(description="Deploy the TV Box arcade stack (dry-run unless --apply).")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", default="")
    parser.add_argument("--apply", action="store_true", help="Actually change the device (default: print plan)")
    parser.add_argument("--su", action="store_true", help="Device is rooted: allow su for privileged steps")
    parser.add_argument("--rollback-file", default="tvbox_rollback.json")
    parser.add_argument("--backup-dir", default="tvbox_backup", help="Host folder for backups of replaced files")
    parser.add_argument("--retroarch-apk", default="")
    parser.add_argument("--pegasus-apk", default="")
    parser.add_argument("--cores-dir", default="", help="Host folder with armeabi-v7a *_libretro_android.so")
    parser.add_argument("--configs", action="store_true", help="Push retroarch.cfg, core options, autoconfig, Pegasus settings")
    parser.add_argument("--locale", default="", help="Override general.locale in Pegasus settings (e.g. en, es)")
    parser.add_argument("--theme-dir", default="", help="Local theme folder (e.g. RGLegacy) to lint and push")
    parser.add_argument("--force-theme", action="store_true", help="Push theme even if the Qt 5.15 lint fails")
    parser.add_argument("--game-dirs", default="", help="Local game_dirs.txt to push to Pegasus")
    parser.add_argument("--keylayouts", action="store_true", help="Install .kl files into /system (needs --su)")
    parser.add_argument("--android-kiosk", action="store_true", help="Disable animations, screensaver and sleep")
    parser.add_argument("--disable-packages", default="", help="Comma-separated packages to pm disable-user")
    parser.add_argument("--set-home", action="store_true", help="Make Pegasus the default HOME launcher")
    args = parser.parse_args()

    try:
        ensure_device(args.adb, args.serial)
    except AdbError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    adb = Adb(args.adb, args.serial, apply=args.apply, use_su=args.su)
    rb = Rollback(args.rollback_file, args.serial)
    d = Deployer(adb, rb, args.backup_dir)
    print("MODE:", "APPLY (device will be changed)" if args.apply else "DRY-RUN (nothing is changed; add --apply)")

    try:
        apks = [a for a in (args.retroarch_apk, args.pegasus_apk) if a]
        if apks:
            d.install_apks(apks)
        if args.cores_dir:
            d.cores(args.cores_dir)
        if args.configs:
            d.configs(args.locale)
        if args.theme_dir:
            d.theme(args.theme_dir, args.force_theme)
        if args.game_dirs:
            d.game_dirs(args.game_dirs)
        if args.keylayouts:
            d.keylayouts()
        if args.android_kiosk:
            d.android_kiosk()
        pkgs = [p.strip() for p in args.disable_packages.split(",") if p.strip()]
        if pkgs:
            d.debloat(pkgs)
        if args.set_home:
            d.set_home()
    except AdbError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        if args.apply:
            print(f"Partial changes are recorded in {args.rollback_file}; undo with rollback_tvbox.py", file=sys.stderr)
        sys.exit(1)

    if args.apply:
        print(f"\nDone. Rollback data: {os.path.abspath(args.rollback_file)}")
        print("Run verify_tvbox.py next. When finished, turn off network ADB on public cabinets.")
    else:
        print("\nDry-run complete. Re-run with --apply after the user confirms the plan.")


if __name__ == "__main__":
    main()
