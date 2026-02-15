"""
macOS menu bar app: shows primary IP in the bar; menu shows all IPs,
ping (gateway, google), speedtest, and LAN scanner.
"""
import ipaddress
import os
import re
import socket
import subprocess
import sys

import psutil
import rumps

# Smaller menu bar font (optional; requires PyObjC AppKit)
try:
    from AppKit import NSFont, NSAttributedString, NSFontAttributeName
    _MENUBAR_FONT_SIZE = 10
    _HAVE_APPKIT = True
except ImportError:
    _HAVE_APPKIT = False


# --- IP addresses (primary = highest-priority non-VPN interface, all = every interface) ---

# Interface name prefixes to ignore everywhere (VPN/tunnel, bridge)
_IGNORED_INTERFACE_PREFIXES = ("utun", "ppp", "ipsec", "bridge")

def _is_ignored_interface(iface):
    """True if interface should be ignored (VPN, bridge, etc.)."""
    if not iface:
        return True
    low = iface.lower()
    return any(low.startswith(p) for p in _IGNORED_INTERFACE_PREFIXES)


def _service_order_interfaces():
    """
    Get ordered list of (service_name, device) from macOS network service order.
    Skips disabled (*), VPN-like devices, and services whose name contains "VPN".
    Device is e.g. 'en0', 'en1'.
    """
    try:
        out = subprocess.check_output(
            ["networksetup", "-listnetworkserviceorder"],
            text=True,
            timeout=3,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    # Format: "(1) Wi-Fi (Hardware Port: Wi-Fi, Device: en0)" or "(*) VPN (..., Device: utun3)"
    order = []
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.startswith("("):
            continue
        disabled = line.startswith("(*)")
        if disabled:
            continue
        # (1) Service Name (Hardware Port: ..., Device: en0)
        match = re.match(r"\(\d+\)\s+(.+?)\s+\([^)]*Device:\s*(\w+)\)", line)
        if not match:
            continue
        service_name, device = match.group(1).strip(), match.group(2).strip()
        if "vpn" in service_name.lower():
            continue
        if _is_ignored_interface(device):
            continue
        order.append((service_name, device))
    return order


def _first_non_vpn_ip_from_psutil():
    """First IPv4 address from a non-ignored interface (by device name), sorted en0, en1, ... for predictability."""
    try:
        items = []
        for iface, addrs in psutil.net_if_addrs().items():
            if _is_ignored_interface(iface):
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address and not addr.address.startswith("127."):
                    items.append((iface, addr.address))
                    break
        if not items:
            return None
        # Prefer en* (Ethernet often en0, Wi‑Fi en1), then sort by name
        items.sort(key=lambda x: (not x[0].startswith("en"), x[0]))
        return items[0][1]
    except Exception:
        return None


def get_primary_ip():
    """
    IP of the highest-priority non-VPN network interface (macOS service order).
    Excludes VPN/virtual interfaces so the menu bar shows your Ethernet/LAN IP.
    """
    for _service_name, device in _service_order_interfaces():
        try:
            addrs = psutil.net_if_addrs().get(device)
            if not addrs:
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address and not addr.address.startswith("127."):
                    return addr.address
        except (KeyError, AttributeError):
            continue
    # Fallback without using socket (socket would give VPN IP if VPN is default route)
    ip = _first_non_vpn_ip_from_psutil()
    if ip:
        return ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def get_all_ips():
    """All IPv4 addresses by interface (bridge etc. ignored). Returns list of (interface_name, address)."""
    result = []
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            if _is_ignored_interface(iface):
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    result.append((iface, addr.address))
    except Exception:
        pass
    return result


# --- LAN Scanner: available networks (subnet per interface) ---

def get_subnet_for_device(device):
    """Return CIDR string (e.g. '192.168.1.0/24') for device, or None."""
    try:
        addrs = psutil.net_if_addrs().get(device)
        if not addrs:
            return None
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address and addr.netmask and not addr.address.startswith("127."):
                net = ipaddress.IPv4Network(f"{addr.address}/{addr.netmask}", strict=False)
                return str(net)
    except Exception:
        pass
    return None


def get_available_networks():
    """
    All interfaces that have an IPv4 subnet (for LAN scan).
    Returns list of (display_name, cidr) in a stable order.
    Uses macOS service name (e.g. Ethernet, Wi-Fi) when available, else device name (e.g. en0).
    """
    device_to_name = {}
    for service_name, device in _service_order_interfaces():
        if device not in device_to_name:
            device_to_name[device] = service_name
    seen_cidrs = set()
    result = []
    for device, name in sorted(device_to_name.items(), key=lambda x: x[0]):
        cidr = get_subnet_for_device(device)
        if cidr and cidr not in seen_cidrs:
            seen_cidrs.add(cidr)
            result.append((name, cidr))
    for iface, addrs in sorted(psutil.net_if_addrs().items()):
        if _is_ignored_interface(iface):
            continue
        if iface in device_to_name:
            continue
        cidr = get_subnet_for_device(iface)
        if cidr and cidr not in seen_cidrs:
            seen_cidrs.add(cidr)
            result.append((iface, cidr))
    return result


# Paths (next to this script)
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_LAN_SCAN_SCRIPT = os.path.join(_APP_DIR, "lan_scan.py")
# Place icon.png here (e.g. 22×22 or 44×44 PNG); menu bar shows icon only when present
_ICON_PATH = os.path.join(_APP_DIR, "icon.png")


def _get_app_bundle_path():
    """If running from inside an .app bundle, return the path to the .app; else None."""
    path = os.path.abspath(__file__)
    if ".app/Contents" not in path:
        return None
    # Walk up from __file__ to find the .app
    dir_ = os.path.dirname(path)
    while dir_ and dir_ != "/":
        if dir_.endswith(".app"):
            return dir_
        dir_ = os.path.dirname(dir_)
    return None


def _launch_at_login_enabled():
    """True if this app is in the user's Login Items."""
    app_path = _get_app_bundle_path()
    if not app_path:
        return False
    try:
        script = f'''
        tell application "System Events"
            set loginItems to name of every login item
            return "LAIN-tools" in loginItems
        end tell
        '''
        out = subprocess.check_output(["osascript", "-e", script], text=True, timeout=5)
        return "true" in out.lower()
    except Exception:
        return False


def _set_launch_at_login(enabled):
    """Add or remove this app from Login Items."""
    app_path = _get_app_bundle_path()
    if not app_path:
        return False
    # Escape backslashes and quotes for AppleScript string
    app_path_safe = app_path.replace("\\", "\\\\").replace('"', '\\"')
    try:
        if enabled:
            script = f'tell application "System Events" to make login item at end with properties {{path:POSIX file "{app_path_safe}", hidden:false}}'
        else:
            script = 'tell application "System Events" to delete login item "LAIN-tools"'
        subprocess.run(["osascript", "-e", script], check=True, timeout=5)
        return True
    except Exception:
        return False


# --- Open Terminal and run a command ---

def run_in_terminal(command):
    """Open macOS Terminal in a new window and run the given command."""
    # Escape backslash and double-quote for AppleScript string
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Terminal" to do script "{escaped}"'
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# --- Default gateway ---

def get_gateway():
    """Default gateway IP (macOS)."""
    try:
        out = subprocess.check_output(
            ["route", "-n", "get", "default"],
            text=True,
            timeout=2,
        )
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("gateway:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


# --- Ping ---

def ping_host(host, count=3):
    """Ping host. Returns (success: bool, message: str)."""
    if not host:
        return False, "No host"
    try:
        out = subprocess.run(
            ["ping", "-c", str(count), "-t", "3", host],
            capture_output=True,
            text=True,
            timeout=count * 3 + 2,
        )
        if out.returncode != 0:
            return False, "Request timeout or unreachable"
        # Parse approximate RTT from last line: "round-trip min/avg/max = ..."
        match = re.search(r"round-trip min/avg/max[^=]*=\s*[\d.]+/([\d.]+)/[\d.]+", out.stdout)
        if match:
            return True, f"OK — {match.group(1)} ms avg"
        return True, "OK"
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


# --- App ---

class NetStatusApp(rumps.App):
    def __init__(self):
        use_icon = os.path.isfile(_ICON_PATH)
        if use_icon:
            super().__init__("LAIN-tools", title=None, icon=_ICON_PATH, quit_button=None)
        else:
            super().__init__("LAIN-tools", title="…", quit_button=None)
        self._use_icon = use_icon

        # Refresh primary IP and menu (including IP list) every few seconds
        self._timer = rumps.Timer(self._update_title, 3.0)
        self._timer.start()

        self._update_title(None)

    def _build_menu(self):
        """Build menu with all IPs as non-clickable items at top, then actions."""
        primary = get_primary_ip()
        all_ips = get_all_ips()
        sorted_ips = sorted(all_ips, key=lambda x: (x[1] != primary, x[0]))
        ip_items = [
            rumps.MenuItem(f"  {iface}: {addr}", callback=None)
            for iface, addr in sorted_ips
        ]
        def make_scan_callback(cidr, with_ports=False):
            def handler(_):
                self._run_lan_scan(cidr, with_ports=with_ports)
            return handler

        available = get_available_networks()
        lan_scan_items = []
        for name, cidr in available:
            lan_scan_items.append(rumps.MenuItem(f"Scan {name} ({cidr})", callback=make_scan_callback(cidr)))
            lan_scan_items.append(rumps.MenuItem(f"Scan {name} ({cidr}) — ports", callback=make_scan_callback(cidr, with_ports=True)))
        lan_scanner_submenu = (
            rumps.MenuItem("LAN Scanner"),
            lan_scan_items if lan_scan_items else [rumps.MenuItem("No networks with subnet", callback=None)],
        )
        menu_parts = ip_items + [
            rumps.separator,
            rumps.MenuItem("All IP addresses", callback=self.show_all_ips),
            rumps.separator,
            rumps.MenuItem("Ping gateway", callback=self.ping_gateway),
            rumps.MenuItem("Ping google.com", callback=self.ping_google),
            rumps.MenuItem("Speedtest", callback=self.run_speedtest_menu),
            rumps.separator,
            lan_scanner_submenu,
        ]
        if _get_app_bundle_path():
            launch_item = rumps.MenuItem("Launch at Login", callback=self._toggle_launch_at_login)
            launch_item.state = 1 if _launch_at_login_enabled() else 0
            menu_parts.append(rumps.separator)
            menu_parts.append(launch_item)
        menu_parts.extend([
            rumps.separator,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ])
        return menu_parts

    def _update_title(self, _):
        if not self._use_icon:
            primary = get_primary_ip()
            title_text = primary if primary else "No network"
            self.title = title_text
            if _HAVE_APPKIT:
                try:
                    nsitem = self._nsapp.nsstatusitem
                    font = NSFont.monospacedDigitSystemFontOfSize_weight_(_MENUBAR_FONT_SIZE, 0.0)
                    attr = NSAttributedString.alloc().initWithString_attributes_(
                        title_text, {NSFontAttributeName: font}
                    )
                    nsitem.setAttributedTitle_(attr)
                except Exception:
                    pass
        # Refresh dropdown menu so IP list is up to date
        try:
            self._menu.clear()
            self.menu = self._build_menu()
        except Exception:
            pass

    @rumps.clicked("All IP addresses")
    def show_all_ips(self, _):
        all_ips = get_all_ips()
        primary = get_primary_ip()
        lines = []
        for iface, addr in sorted(all_ips, key=lambda x: (x[1] != primary, x[0])):
            mark = " (primary)" if addr == primary else ""
            lines.append(f"  {iface}: {addr}{mark}")
        text = "\n".join(lines) if lines else "No addresses found."
        rumps.alert("All IP addresses", text)

    @rumps.clicked("Ping gateway")
    def ping_gateway(self, _):
        gw = get_gateway()
        if not gw:
            rumps.alert("Ping gateway", "Could not get default gateway.")
            return
        run_in_terminal(f'bash -c \'ping -c 5 {gw}; echo; read -p "Press Enter to close..."\'')

    @rumps.clicked("Ping google.com")
    def ping_google(self, _):
        run_in_terminal('bash -c \'ping -c 5 google.com; echo; read -p "Press Enter to close..."\'')

    @rumps.clicked("Speedtest")
    def run_speedtest_menu(self, _):
        # Use same Python as this app (e.g. venv) so speedtest module is found
        python_exe = sys.executable.replace("'", "'\"'\"'")
        run_in_terminal(f'bash -c \'"{python_exe}" -m speedtest; echo; read -p "Press Enter to close..."\'')

    def _run_lan_scan(self, cidr, with_ports=False):
        python_exe = sys.executable.replace("'", "'\"'\"'")
        script = _LAN_SCAN_SCRIPT.replace("'", "'\"'\"'")
        ports_arg = " --ports" if with_ports else ""
        run_in_terminal(f'bash -c \'"{python_exe}" "{script}" {cidr}{ports_arg}; echo; read -p "Press Enter to close..."\'')

    def _toggle_launch_at_login(self, _):
        currently = _launch_at_login_enabled()
        if _set_launch_at_login(not currently):
            rumps.notification(
                "LAIN-tools",
                "Launch at Login",
                "On — will start when you log in." if not currently else "Off — removed from Login Items.",
            )


if __name__ == "__main__":
    NetStatusApp().run()
