"""Read-only ADB hardware & capability audit for Android TV Boxes (RK3229/RK322x).

Collects SoC, ABI, Android SDK, RAM, storage type (rknand vs eMMC), /system and
/data free space, GLES renderer, root availability, SoC temperature, mounted
USB/SD volumes, display size, installed arcade packages, current HOME launcher,
CPU frequencies, and attached USB controllers without modifying any device state.

All probes run in a single `adb shell` round trip (much faster over Wi-Fi).
"""
import argparse
import json
import re
import subprocess
import sys

from tvbox_adb import AdbError, adb_argv, ensure_device

MARKER = "@@TVBOX_PROBE@@"

PROBES = {
    "abi": "getprop ro.product.cpu.abi",
    "sdk": "getprop ro.build.version.sdk",
    "release": "getprop ro.build.version.release",
    "board": "getprop ro.product.board",
    "platform": "getprop ro.board.platform",
    "hardware": "getprop ro.hardware",
    "uname": "uname -a",
    "meminfo": "cat /proc/meminfo",
    "partitions": "cat /proc/partitions",
    "cmdline": "cat /proc/cmdline",
    "df": "df /system /data",
    "home": "cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN",
    "cpu_freqs": "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies",
    "governor": "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
    "inputs": "cat /proc/bus/input/devices",
    "gles": "dumpsys SurfaceFlinger | grep -i -m 2 GLES",
    "thermal": "cat /sys/class/thermal/thermal_zone*/temp",
    "storage": "ls /storage",
    "wm_size": "wm size",
    "packages": "pm list packages",
    # Last on purpose: a superuser prompt on the TV may block until the timeout.
    "root": "su -c id",
}

ARCADE_PACKAGES = {
    "retroarch": "com.retroarch.ra32",
    "pegasus": "org.pegasus_frontend.android",
}

KNOWN_CONTROLLERS = {
    "0079:0006": "Zero Delay USB encoder (0079:0006) detected: deploy Vendor_0079_Product_0006.kl + DragonRise_ZeroDelay.cfg.",
    "054c:0268": "Dual-mode gamepad in PS3 mode (054c:0268): if unresponsive on kernel 3.10, hold Mode/PS to switch to 0e8f:0003.",
    "0e8f:0003": "Dual-mode gamepad in PC mode (0e8f:0003): deploy Vendor_0e8f_Product_0003.kl + MyPower_PS3_PC.cfg.",
}


def build_batch_script(probes: dict[str, str]) -> str:
    """Join every probe into one shell script whose sections are delimited by MARKER lines."""
    parts = [f"echo '{MARKER} {key}'; {cmd} 2>/dev/null" for key, cmd in probes.items()]
    return "; ".join(parts)


def parse_batch_output(output: str) -> dict[str, str]:
    raw: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in output.replace("\r\n", "\n").split("\n"):
        if line.startswith(MARKER):
            if current is not None:
                raw[current] = "\n".join(buf).strip()
            current = line[len(MARKER):].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        raw[current] = "\n".join(buf).strip()
    return raw


def run_probes(adb_bin: str, serial: str, timeout: int = 60) -> dict[str, str]:
    try:
        proc = subprocess.run(
            adb_argv(adb_bin, serial, "shell", build_batch_script(PROBES)),
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # Keep whatever was collected (usually only the final `su` probe hung).
        partial = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        if MARKER not in partial:
            raise AdbError("adb shell timed out before returning any probe output") from exc
        return parse_batch_output(partial)
    if proc.returncode != 0 and MARKER not in (proc.stdout or ""):
        raise AdbError(f"adb shell failed: {(proc.stderr or '').strip()}")
    return parse_batch_output(proc.stdout or "")


def parse_mem_mb(meminfo_text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in ("MemTotal", "MemAvailable", "MemFree"):
        m = re.search(rf"^{key}:\s+(\d+)\s+kB", meminfo_text, re.M)
        if m:
            out[key] = round(int(m.group(1)) / 1024)
    return out


def parse_input_devices(devices_text: str) -> list[dict[str, str]]:
    """Extract USB HID input devices (VID:PID and name) from /proc/bus/input/devices."""
    found: list[dict[str, str]] = []
    for block in devices_text.split("\n\n"):
        bus_m = re.search(r"Bus=([0-9a-fA-F]+)\s+Vendor=([0-9a-fA-F]+)\s+Product=([0-9a-fA-F]+)", block)
        name_m = re.search(r'N:\s+Name="([^"]+)"', block)
        if not bus_m or not name_m:
            continue
        bus, vid, pid = bus_m.group(1).lower(), bus_m.group(2).lower(), bus_m.group(3).lower()
        if bus == "0003":  # USB
            found.append({"vid_pid": f"{vid}:{pid}", "name": name_m.group(1)})
    return found


def _size_to_mb(token: str) -> float | None:
    """Convert toybox/toolbox df sizes ('12M', '1.2G', '512K', '20480' 1K-blocks) to MB."""
    m = re.fullmatch(r"([\d.]+)([KMGT]?)", token.strip(), re.I)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).upper()
    factor = {"": 1 / 1024, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}[unit]
    return round(value * factor, 1)


def parse_df_free_mb(df_text: str) -> dict[str, float]:
    """Return {mountpoint: free_MB} for /system and /data from `df` (toolbox or toybox layout)."""
    free: dict[str, float] = {}
    for line in df_text.splitlines():
        cols = line.split()
        if len(cols) < 4 or cols[0].lower() in ("filesystem", "filesystem:"):
            continue
        mount = next((c for c in cols if c in ("/system", "/data")), None)
        if not mount:
            continue
        # Both layouts keep free space in the 4th column:
        #   toolbox (Android 7): "<mount> Size Used Free Blksize"           (human sizes)
        #   toybox:              "<dev> 1K-blocks Used Available Use% <mount>" (1K blocks)
        mb = _size_to_mb(cols[3])
        if mb is not None:
            free[mount] = mb
    return free


def parse_thermal_c(thermal_text: str) -> list[float]:
    temps: list[float] = []
    for tok in thermal_text.split():
        if tok.lstrip("-").isdigit():
            v = int(tok)
            temps.append(round(v / 1000, 1) if abs(v) > 200 else float(v))
    return temps


def parse_external_volumes(storage_text: str) -> list[str]:
    """List FAT/exFAT volume serials mounted under /storage (e.g. 1234-ABCD)."""
    return [v for v in storage_text.split() if re.fullmatch(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}", v)]


def build_report(raw: dict[str, str]) -> dict:
    abi = raw.get("abi", "")
    sdk = int(raw["sdk"]) if raw.get("sdk", "").isdigit() else 0
    partitions = raw.get("partitions", "")
    cmdline = raw.get("cmdline", "")
    mem = parse_mem_mb(raw.get("meminfo", ""))
    usb_inputs = parse_input_devices(raw.get("inputs", ""))
    free_mb = parse_df_free_mb(raw.get("df", ""))
    temps = parse_thermal_c(raw.get("thermal", ""))
    volumes = parse_external_volumes(raw.get("storage", ""))
    root_available = "uid=0" in raw.get("root", "")
    packages_text = raw.get("packages", "")
    installed = {name: f"package:{pkg}" in packages_text.split() for name, pkg in ARCADE_PACKAGES.items()}
    wm_m = re.search(r"(\d+)x(\d+)", raw.get("wm_size", ""))

    is_raw_nand = ("rknand" in partitions) or ("storagemedia=nand" in cmdline)
    storage_type = "raw_nand" if is_raw_nand else ("emmc" if "mmcblk" in partitions else "unknown")

    warnings: list[str] = []
    if is_raw_nand:
        warnings.append(
            "Raw NAND (rknand) detected: NEVER erase/flash NAND blindly; keep Android + RetroArch + Pegasus path."
        )
    if 0 < sdk < 26:
        warnings.append(
            f"Android API {sdk} (< 26): modern Daijisho will fail with INSTALL_FAILED_OLDER_SDK; use 32-bit Pegasus Frontend."
        )
    if mem.get("MemTotal", 0) and mem["MemTotal"] <= 1100:
        warnings.append(
            f"Low RAM ({mem['MemTotal']} MB): keep rewind/run_ahead disabled and store ROMs on external USB/SD."
        )
    if "/system" in free_mb and free_mb["/system"] < 20:
        warnings.append(
            f"/system has only {free_mb['/system']} MB free: delete old bootanimation.* BEFORE copying replacements."
        )
    if "/data" in free_mb and free_mb["/data"] < 5 * 1024:
        warnings.append(
            f"/data has {free_mb['/data']} MB free (< 5 GB): keep ROMs on external USB/SD storage."
        )
    if temps and max(temps) >= 75:
        warnings.append(f"SoC temperature {max(temps)} C: add a heatsink/fan before long play sessions.")
    if not root_available:
        warnings.append(
            "No root (su): /system keylayouts, boot logo and pushing cores into /data/data need root; "
            "use RetroArch Online Updater for cores instead."
        )
    if not volumes:
        warnings.append("No external FAT/exFAT volume under /storage: plug the ROM USB/SD drive before Phase 3.")

    for dev in usb_inputs:
        if dev["vid_pid"] in KNOWN_CONTROLLERS:
            warnings.append(KNOWN_CONTROLLERS[dev["vid_pid"]])

    return {
        "abi": abi,
        "sdk": sdk,
        "android_release": raw.get("release", ""),
        "board": raw.get("board", ""),
        "platform": raw.get("platform", ""),
        "hardware": raw.get("hardware", ""),
        "kernel": raw.get("uname", ""),
        "gles": raw.get("gles", ""),
        "memory_mb": mem,
        "storage_type": storage_type,
        "is_raw_nand": is_raw_nand,
        "free_mb": free_mb,
        "root_available": root_available,
        "soc_temps_c": temps,
        "external_volumes": volumes,
        "display_size": f"{wm_m.group(1)}x{wm_m.group(2)}" if wm_m else "",
        "installed": installed,
        "cpu_freqs_khz": raw.get("cpu_freqs", "").split(),
        "cpu_governor": raw.get("governor", ""),
        "home_launcher": raw.get("home", ""),
        "disk_free": raw.get("df", ""),
        "usb_input_devices": usb_inputs,
        "recommended_retroarch": "RetroArch_ra32 (armeabi-v7a)" if "armeabi-v7a" in abi else "Match device ABI",
        "recommended_frontend": "Pegasus Frontend 32-bit + RGLegacy (Qt 5.15)",
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only ADB hardware audit for TV Box Arcade Builder.")
    parser.add_argument("--adb", default="adb", help="Path to adb executable (default: adb)")
    parser.add_argument("--serial", default="", help="Optional ADB device serial or IP:5555")
    parser.add_argument("--json", action="store_true", help="Output structured JSON report")
    args = parser.parse_args()

    try:
        ensure_device(args.adb, args.serial)
        raw = run_probes(args.adb, args.serial)
    except AdbError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    report = build_report(raw)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    print("=== TV Box Arcade Hardware Audit (Read-Only) ===")
    print(f"Board / Platform : {report['board']} / {report['platform']} ({report['hardware']})")
    print(f"Android / ABI    : Android {report['android_release']} (SDK {report['sdk']}) · {report['abi']}")
    print(f"GPU              : {report['gles'] or 'unknown'}")
    print(f"Display          : {report['display_size'] or 'unknown'}")
    print(f"RAM (MB)         : {report['memory_mb']}")
    print(f"Storage Type     : {report['storage_type']} (Raw NAND: {report['is_raw_nand']})")
    print(f"Free space (MB)  : {report['free_mb']}")
    print(f"External volumes : {', '.join(report['external_volumes']) or 'none'}")
    print(f"Root (su)        : {report['root_available']}")
    print(f"SoC temps (C)    : {report['soc_temps_c']}")
    print(f"CPU Governor     : {report['cpu_governor']} | Freqs: {' '.join(report['cpu_freqs_khz'])}")
    print(f"Current HOME     : {report['home_launcher']}")
    print(f"Installed        : {report['installed']}")
    print(f"USB Inputs       : {report['usb_input_devices']}")
    print(f"Recommendation   : {report['recommended_retroarch']} + {report['recommended_frontend']}")
    if report["warnings"]:
        print("\nGuardrails & Findings:")
        for w in report["warnings"]:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
