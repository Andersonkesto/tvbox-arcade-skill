"""Automate the SKILL.md Verification Checklist on a live TV Box (read-only by default).

Checks:
  lastrun   Pegasus lastrun.log has no QML Syntax/Reference/Type errors (+ theme loaded)
  focus     Pegasus is the focused window
  home      Pegasus is the default HOME activity          (--expect-home)
  bgm       AudioFlinger has an active, non-standby track (--expect-bgm)
  screencap Pulls a screenshot for visual inspection      (--screenshot out.png)
  launch    Starts one ROM in RetroArch and checks focus  (--test-rom + --test-core, opt-in)

Exit code: 0 = all checks passed, 1 = at least one FAIL, 2 = no device.
"""
import argparse
import json
import re
import subprocess
import sys
import time

from tvbox_adb import Adb, AdbError, ensure_device

PEGASUS_PKG = "org.pegasus_frontend.android"
LASTRUN = f"/sdcard/Android/data/{PEGASUS_PKG}/files/pegasus-frontend/lastrun.log"
QML_ERROR_RX = re.compile(r"(Syntax error|ReferenceError|TypeError|is not a type|module \"[^\"]+\" is not installed)")
GAME_COUNT_RX = re.compile(r"(\d+)\s+games?", re.I)


def check_lastrun(log_text: str, theme: str = "") -> dict:
    if not log_text.strip():
        return {"status": "FAIL", "detail": f"{LASTRUN} missing or empty (has Pegasus run yet?)"}
    errors = [ln.strip() for ln in log_text.splitlines() if QML_ERROR_RX.search(ln)]
    counts = [int(m.group(1)) for m in GAME_COUNT_RX.finditer(log_text)]
    info = f"games reported: {max(counts)}" if counts else "game count not found in log"
    if errors:
        return {"status": "FAIL", "detail": f"{len(errors)} QML error(s); first: {errors[0]}", "errors": errors[:20]}
    if theme and theme.lower() not in log_text.lower():
        return {"status": "FAIL", "detail": f"theme '{theme}' not mentioned in lastrun.log ({info})"}
    return {"status": "PASS", "detail": f"no QML errors; {info}"}


def check_focus(window_text: str, pkg: str = PEGASUS_PKG) -> dict:
    m = re.search(r"mCurrentFocus=(.*)", window_text)
    focus = m.group(1).strip() if m else ""
    ok = pkg in focus
    return {"status": "PASS" if ok else "FAIL", "detail": focus or "mCurrentFocus not found"}


def check_home(resolve_text: str, pkg: str = PEGASUS_PKG) -> dict:
    last = resolve_text.strip().splitlines()[-1] if resolve_text.strip() else ""
    return {"status": "PASS" if pkg in last else "FAIL", "detail": last or "no HOME activity resolved"}


def check_bgm(audio_flinger_text: str) -> dict:
    standby_no = re.search(r"Standby:\s*no", audio_flinger_text) is not None
    active = re.search(r"(\d+)\s+Tracks? of which\s+(\d+)\s+are active", audio_flinger_text)
    n_active = int(active.group(2)) if active else 0
    ok = standby_no and n_active >= 1
    return {"status": "PASS" if ok else "FAIL", "detail": f"standby={'no' if standby_no else 'yes'}, active tracks={n_active}"}


def main():
    parser = argparse.ArgumentParser(description="Verify a finished TV Box arcade setup over ADB.")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", default="")
    parser.add_argument("--theme", default="RGLegacy", help="Theme name expected in lastrun.log ('' to skip)")
    parser.add_argument("--expect-home", action="store_true", help="Require Pegasus as default HOME")
    parser.add_argument("--expect-bgm", action="store_true", help="Require background music to be playing")
    parser.add_argument("--screenshot", default="", help="Save a screencap PNG to this host path")
    parser.add_argument("--test-rom", default="", help="Device path of a ROM to launch (opt-in, changes foreground)")
    parser.add_argument("--test-core", default="", help="Core .so for --test-rom (e.g. fbneo_libretro_android.so)")
    parser.add_argument("--retroarch-pkg", default="com.retroarch.ra32")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        ensure_device(args.adb, args.serial)
    except AdbError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    adb = Adb(args.adb, args.serial)

    results: dict[str, dict] = {}
    results["lastrun"] = check_lastrun(adb.query(f"tail -n 300 {LASTRUN} 2>/dev/null"), args.theme)
    results["focus"] = check_focus(adb.query("dumpsys window windows | grep mCurrentFocus"))
    if args.expect_home:
        results["home"] = check_home(adb.query(
            "cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN"))
    if args.expect_bgm:
        results["bgm"] = check_bgm(adb.query("dumpsys media.audio_flinger"))

    if args.screenshot:
        with open(args.screenshot, "wb") as fh:
            proc = subprocess.run(adb.argv("exec-out", "screencap", "-p"), stdout=fh, check=False)
        ok = proc.returncode == 0
        results["screencap"] = {"status": "PASS" if ok else "FAIL",
                                "detail": f"saved {args.screenshot} - inspect cards, covers, TopBar, splash"}

    if args.test_rom:
        if not args.test_core:
            parser.error("--test-rom requires --test-core")
        pkg = args.retroarch_pkg
        adb.query(
            f"am start -n {pkg}/com.retroarch.browser.retroactivity.RetroActivityFuture"
            f" -e ROM '{args.test_rom}' -e LIBRETRO /data/data/{pkg}/cores/{args.test_core}"
            f" -e CONFIGFILE /storage/emulated/0/Android/data/{pkg}/files/retroarch.cfg"
            " --activity-clear-task --activity-clear-top --activity-no-history"
        )
        time.sleep(6)
        focus = check_focus(adb.query("dumpsys window windows | grep mCurrentFocus"), pkg)
        alive = bool(adb.query(f"pidof {pkg}"))
        focus["status"] = "PASS" if focus["status"] == "PASS" and alive else "FAIL"
        results["launch"] = focus
        adb.query(f"am force-stop {pkg}")

    failed = [k for k, v in results.items() if v["status"] != "PASS"]
    if args.json:
        print(json.dumps({"passed": not failed, "checks": results}, indent=2, ensure_ascii=False))
    else:
        for name, r in results.items():
            print(f"[{r['status']}] {name:<9} {r['detail']}")
        print("\nALL CHECKS PASSED" if not failed else f"\nFAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
