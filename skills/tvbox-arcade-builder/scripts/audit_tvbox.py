"""Read-only ADB hardware & capability audit for Android TV Boxes (RK3229/RK322x).

Collects SoC, ABI, Android SDK, RAM, storage type (rknand vs eMMC), /system and
/data free space, current HOME launcher, CPU frequencies, and attached USB
controllers without modifying any device state.
"""
import argparse
import json
import re
import subprocess


def run_adb_shell(adb_bin: str, serial: str, cmd: str, timeout: int = 12) -> str:
    argv = [adb_bin]
    if serial:
        argv.extend(["-s", serial])
    argv.extend(["shell", cmd])
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    return (proc.stdout or "").strip()


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


def build_report(raw: dict[str, str]) -> dict:
    abi = raw.get("abi", "")
    sdk = int(raw["sdk"]) if raw.get("sdk", "").isdigit() else 0
    partitions = raw.get("partitions", "")
    cmdline = raw.get("cmdline", "")
    mem = parse_mem_mb(raw.get("meminfo", ""))
    usb_inputs = parse_input_devices(raw.get("inputs", ""))

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

    for dev in usb_inputs:
        if dev["vid_pid"] == "0079:0006":
            warnings.append("Zero Delay USB encoder (0079:0006) detected: deploy Vendor_0079_Product_0006.kl + DragonRise_ZeroDelay.cfg.")
        elif dev["vid_pid"] == "054c:0268":
            warnings.append("Dual-mode gamepad in PS3 mode (054c:0268): if unresponsive on kernel 3.10, hold Mode/PS to switch to 0e8f:0003.")
        elif dev["vid_pid"] == "0e8f:0003":
            warnings.append("Dual-mode gamepad in PC mode (0e8f:0003): deploy Vendor_0e8f_Product_0003.kl + MyPower_PS3_PC.cfg.")

    return {
        "abi": abi,
        "sdk": sdk,
        "android_release": raw.get("release", ""),
        "board": raw.get("board", ""),
        "hardware": raw.get("hardware", ""),
        "kernel": raw.get("uname", ""),
        "memory_mb": mem,
        "storage_type": storage_type,
        "is_raw_nand": is_raw_nand,
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

    probes = {
        "abi": "getprop ro.product.cpu.abi",
        "sdk": "getprop ro.build.version.sdk",
        "release": "getprop ro.build.version.release",
        "board": "getprop ro.product.board",
        "hardware": "getprop ro.hardware",
        "uname": "uname -a",
        "meminfo": "cat /proc/meminfo",
        "partitions": "cat /proc/partitions",
        "cmdline": "cat /proc/cmdline",
        "df": "df -h /system /data",
        "home": "cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN",
        "cpu_freqs": "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies 2>/dev/null",
        "governor": "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null",
        "inputs": "cat /proc/bus/input/devices",
    }

    raw = {k: run_adb_shell(args.adb, args.serial, cmd) for k, cmd in probes.items()}
    report = build_report(raw)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    print("=== TV Box Arcade Hardware Audit (Read-Only) ===")
    print(f"Board / Hardware : {report['board']} / {report['hardware']}")
    print(f"Android / ABI    : Android {report['android_release']} (SDK {report['sdk']}) · {report['abi']}")
    print(f"RAM (MB)         : {report['memory_mb']}")
    print(f"Storage Type     : {report['storage_type']} (Raw NAND: {report['is_raw_nand']})")
    print(f"CPU Governor     : {report['cpu_governor']} | Freqs: {' '.join(report['cpu_freqs_khz'])}")
    print(f"Current HOME     : {report['home_launcher']}")
    print(f"USB Inputs       : {report['usb_input_devices']}")
    print(f"Recommendation   : {report['recommended_retroarch']} + {report['recommended_frontend']}")
    if report["warnings"]:
        print("\nGuardrails & Findings:")
        for w in report["warnings"]:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
