"""Undo what deploy_tvbox.py changed, using its rollback JSON. DRY-RUN unless --apply.

Restores, in this order: original HOME launcher, disabled packages, Android
settings, /system keylayouts (root), and overwritten config files. Installed
APKs, cores, themes and ROM media are left in place (remove them manually or
with `adb uninstall` if wanted).
"""
import argparse
import json
import os
import sys

from tvbox_adb import Adb, AdbError, ensure_device


def rollback(adb: Adb, data: dict) -> list[str]:
    errors: list[str] = []

    def attempt(label: str, fn) -> None:
        try:
            fn()
        except AdbError as exc:
            errors.append(f"{label}: {exc}")
            print(f"  ERROR {label}: {exc}")

    print("\n== HOME launcher")
    home = (data.get("home") or {}).get("original")
    if home:
        attempt("home", lambda: adb.shell(f"cmd package set-home-activity {home}", privileged=adb.use_su))
    else:
        print("  nothing recorded")

    print("\n== Re-enable packages")
    for pkg in data.get("disabled_packages", []):
        attempt(pkg, lambda p=pkg: adb.shell(f"pm enable {p}", privileged=adb.use_su))

    print("\n== Android settings")
    for s in data.get("settings", []):
        ns, key, prev = s["namespace"], s["key"], s.get("previous", "null")
        cmd = f"settings delete {ns} {key}" if prev in ("", "null") else f"settings put {ns} {key} {prev}"
        attempt(f"{ns}/{key}", lambda c=cmd: adb.shell(c))

    print("\n== /system files (root)")
    for f in data.get("system_files", []):
        remote, backup = f["remote"], f.get("backup")
        restore = f"cp {backup} {remote} && chmod 644 {remote} && chown root:root {remote}" if backup else f"rm -f {remote}"
        attempt(remote, lambda r=restore: adb.shell(
            f"mount -o rw,remount /system && {r}; rc=$?; sync; mount -o ro,remount /system; exit $rc",
            privileged=True))

    print("\n== Config files")
    for f in data.get("files", []):
        remote, backup = f["remote"], f.get("backup")
        if backup:
            if not os.path.isfile(backup):
                errors.append(f"{remote}: host backup missing ({backup})")
                print(f"  ERROR {remote}: host backup missing ({backup})")
                continue
            attempt(remote, lambda b=backup, r=remote: adb.push(b, r))
        else:
            attempt(remote, lambda r=remote: adb.shell(f"rm -f '{r}'"))
    return errors


def main():
    parser = argparse.ArgumentParser(description="Roll back deploy_tvbox.py changes (dry-run unless --apply).")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", default="", help="Defaults to the serial stored in the rollback file")
    parser.add_argument("--rollback-file", default="tvbox_rollback.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--su", action="store_true", help="Device is rooted: allow su for privileged steps")
    args = parser.parse_args()

    if not os.path.isfile(args.rollback_file):
        print(f"ERROR: rollback file not found: {args.rollback_file}", file=sys.stderr)
        sys.exit(2)
    with open(args.rollback_file, encoding="utf-8") as f:
        data = json.load(f)
    serial = args.serial or data.get("serial", "")

    try:
        ensure_device(args.adb, serial)
    except AdbError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    print("MODE:", "APPLY" if args.apply else "DRY-RUN (add --apply to restore)")
    errors = rollback(Adb(args.adb, serial, apply=args.apply, use_su=args.su), data)
    if errors:
        print(f"\n{len(errors)} step(s) failed; the rollback file was kept.", file=sys.stderr)
        sys.exit(1)
    if args.apply:
        done = args.rollback_file + ".done"
        os.replace(args.rollback_file, done)
        print(f"\nRollback complete. Record archived as {done}")


if __name__ == "__main__":
    main()
