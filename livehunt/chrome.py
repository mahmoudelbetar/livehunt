"""Find, launch and verify Chrome with the DevTools port open.

The user should never have to remember a flag. `livehunt up` calls `ensure()`, which
checks the port, launches the right Chrome binary with a dedicated profile when the
port is closed, waits for CDP to answer and reports the URL to open.

Chrome 136+ ignores --remote-debugging-port on the default profile, so a separate
--user-data-dir is mandatory. That profile is reused every run, so logins persist.
"""
import os, platform, shutil, subprocess, sys, time
from urllib.parse import urlsplit

DEFAULT_PORT = 9222
PROFILE = os.path.expanduser("~/.livehunt-chrome")

LINUX = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
         "microsoft-edge", "microsoft-edge-stable", "brave-browser"]
MAC = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
       "/Applications/Chromium.app/Contents/MacOS/Chromium",
       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
WINDOWS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
WSL = ["/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
       "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
       "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
       "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe"]


def is_wsl():
    if os.path.exists("/mnt/c/Windows"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def find_binary(explicit=None):
    """Explicit path > LIVE_CHROME > platform candidates. None when nothing is installed."""
    if explicit:
        return explicit
    env = os.environ.get("LIVE_CHROME")
    if env:
        return env
    if sys.platform.startswith("win"):
        candidates = [c for c in WINDOWS if os.path.exists(c)]
    elif sys.platform == "darwin":
        candidates = MAC
    elif is_wsl():
        # The operator's browser is Windows Chrome. Never launch a Linux browser
        # from WSL — it would be a different browser with different logins.
        candidates = [c for c in WSL if os.path.exists(c)]
        return candidates[0] if candidates else None
    else:
        candidates = [shutil.which(c) for c in LINUX]
        candidates = [c for c in candidates if c]
    for c in candidates:
        if c and (os.path.isabs(c) and os.path.exists(c) or shutil.which(c)):
            return c
    return None


def profile_dir(binary=None):
    """Dedicated Chrome profile, reused across runs so logins persist.

    A Windows chrome.exe launched from WSL gets a Windows-side folder, so the
    profile is not a \\\\wsl.localhost UNC path Chrome cannot reliably write to.
    """
    env = os.environ.get("LIVE_CHROME_PROFILE")
    if env:
        return env
    if binary and binary.lower().endswith(".exe") and is_wsl():
        for user in sorted(os.listdir("/mnt/c/Users")):
            if user.lower() in ("default", "default user", "public", "all users"):
                continue
            p = f"/mnt/c/Users/{user}/AppData/Local/livehunt-chrome"
            if os.path.isdir(os.path.dirname(p)):
                return p
    return PROFILE


def native_path(path, binary):
    """A Windows chrome.exe launched from WSL needs a C:\\ style path."""
    if not (binary and binary.lower().endswith(".exe")):
        return path
    if shutil.which("wslpath"):
        try:
            return subprocess.check_output(["wslpath", "-w", path], text=True).strip()
        except Exception:
            pass
    return path


def launch_cmd(binary, port=DEFAULT_PORT, profile=None, url=None):
    profile = profile or profile_dir(binary)
    return [binary, f"--remote-debugging-port={port}",
            f"--user-data-dir={native_path(profile, binary)}",
            "--no-first-run", "--no-default-browser-check",
            url or "about:blank"]


def cdp_port(cdp_url):
    try:
        return urlsplit(cdp_url).port or DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT


def cdp_version(cdp_url, timeout=2):
    try:
        import requests
        return requests.get(cdp_url.rstrip("/") + "/json/version", timeout=timeout).json()
    except Exception:
        return None


def wait_for_cdp(cdp_url, timeout=20, interval=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cdp_version(cdp_url, timeout=1.5)
        if v:
            return v
        time.sleep(interval)
    return None


def ensure(cdp_url, print_fn=print, url=None, timeout=20, explicit=None):
    """Make sure CDP answers. Returns (ok, message). Launches Chrome when needed."""
    v = cdp_version(cdp_url)
    if v:
        return True, f"chrome     already open ({v.get('Browser', 'Chrome')}) on {cdp_url}"
    binary = find_binary(explicit)
    if not binary:
        return False, ("chrome     not found — install Chrome, or set LIVE_CHROME=<path>\n"
                       f"           then run: {binary or 'google-chrome'} "
                       f"--remote-debugging-port={cdp_port(cdp_url)} --user-data-dir={profile_dir()}")
    cmd = launch_cmd(binary, cdp_port(cdp_url), profile_dir(), url)
    print_fn(f"chrome     launching {binary}")
    print_fn(f"chrome     profile {profile_dir()}  (logins persist here)")
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        return False, f"chrome     launch failed: {e}"
    started = time.monotonic()
    v = wait_for_cdp(cdp_url, timeout=timeout)
    if not v:
        return False, (f"chrome     port {cdp_port(cdp_url)} did not open in {timeout}s\n"
                       f"           manual: {' '.join(cmd)}")
    return True, (f"chrome     ready ({v.get('Browser', 'Chrome')}) after "
                  f"{time.monotonic() - started:.1f}s")
