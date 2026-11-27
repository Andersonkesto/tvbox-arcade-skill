"""Small shared ADB helper for the tvbox-arcade-builder scripts.

`Adb` separates read-only queries (always executed) from mutating commands,
which only run when `apply=True`; otherwise they are printed as a dry-run plan.
"""
import shlex
import subprocess


class AdbError(RuntimeError):
    pass


def adb_argv(adb_bin: str, serial: str, *args: str) -> list[str]:
    argv = [adb_bin]
    if serial:
        argv.extend(["-s", serial])
    argv.extend(args)
    return argv


def ensure_device(adb_bin: str, serial: str, timeout: int = 10) -> None:
    """Fail loudly instead of silently producing empty results when no device is reachable."""
    try:
        proc = subprocess.run(
            adb_argv(adb_bin, serial, "get-state"),
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise AdbError(f"adb executable not found: {adb_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError("adb get-state timed out (device unreachable?)") from exc
    state = (proc.stdout or "").strip()
    if proc.returncode != 0 or state != "device":
        detail = (proc.stderr or proc.stdout or "").strip() or f"state={state!r}"
        hint = f" Try: adb connect {serial}" if serial and ":" in serial else ""
        raise AdbError(f"No ADB device ready ({detail}).{hint}")


def su_wrap(cmd: str) -> str:
    return "su -c " + shlex.quote(cmd)


class Adb:
    def __init__(self, adb_bin: str = "adb", serial: str = "", apply: bool = False, use_su: bool = False):
        self.adb_bin = adb_bin
        self.serial = serial
        self.apply = apply
        self.use_su = use_su

    def argv(self, *args: str) -> list[str]:
        return adb_argv(self.adb_bin, self.serial, *args)

    # --- read-only -------------------------------------------------------
    def query(self, cmd: str, privileged: bool = False, timeout: int = 30) -> str:
        """Run a read-only shell command (also in dry-run). Returns stripped stdout."""
        full = su_wrap(cmd) if privileged and self.use_su else cmd
        proc = subprocess.run(self.argv("shell", full), capture_output=True, text=True,
                              timeout=timeout, check=False)
        return (proc.stdout or "").replace("\r\n", "\n").strip()

    def pull(self, remote: str, local: str) -> bool:
        """Read-only copy device -> host (used for backups, also in dry-run)."""
        proc = subprocess.run(self.argv("pull", remote, local), capture_output=True, text=True, check=False)
        return proc.returncode == 0

    # --- mutating --------------------------------------------------------
    def _mutate(self, argv: list[str], label: str, timeout: int = 600) -> bool:
        if not self.apply:
            print(f"  [dry-run] {label}")
            return True
        print(f"  [apply]   {label}")
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode != 0 or "Failure" in out or "Exception" in out:
            raise AdbError(f"Command failed ({label}): {out or proc.returncode}")
        return True

    def shell(self, cmd: str, privileged: bool = False) -> bool:
        if privileged and not self.use_su:
            raise AdbError(f"Root required (pass --su): {cmd}")
        full = su_wrap(cmd) if privileged else cmd
        return self._mutate(self.argv("shell", full), f"adb shell {full}")

    def push(self, local: str, remote: str) -> bool:
        return self._mutate(self.argv("push", local, remote), f"adb push {local} {remote}")

    def install(self, apk: str) -> bool:
        return self._mutate(self.argv("install", "-r", "-g", apk), f"adb install -r -g {apk}")
