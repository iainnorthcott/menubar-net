#!/usr/bin/env python3
"""
Ping-sweep a subnet; print IP + MAC + hostname, or IP + MAC + open ports (--ports).
Usage: python lan_scan.py <CIDR> [--ports]
Example: python lan_scan.py 192.168.1.0/24
         python lan_scan.py 192.168.1.0/24 --ports
"""
import ipaddress
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HOSTNAME_TIMEOUT = 2

# Common TCP ports to scan when --ports
COMMON_PORTS = [
    21, 22, 23, 80, 443, 445, 631, 3306, 3389, 5353, 8080, 9100, 62078,
]


def ping(ip, timeout=1):
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-t", str(timeout), str(ip)],
            capture_output=True,
            timeout=timeout + 1,
        )
        return r.returncode == 0
    except Exception:
        return False


def get_arp_table():
    """Return dict ip -> mac from macOS ARP table. MACs normalized to lowercase with leading zeros."""
    result = {}
    try:
        # -n: numeric (no DNS), often more consistent output
        out = subprocess.check_output(["arp", "-an"], text=True, timeout=5)
    except Exception:
        try:
            out = subprocess.check_output(["arp", "-a"], text=True, timeout=5)
        except Exception:
            return result
    # macOS: "(192.168.1.1) at aa:bb:cc:dd:ee:ff on en0" or "192.168.1.1 at 1:2:3:4:5:6 on en0"
    # Match IP (with or without parens) and "at" MAC (hex:hex:...)
    for line in out.splitlines():
        match = re.search(r"\(?([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)\)?\s+at\s+([0-9a-fA-F:]+)", line)
        if match:
            ip_str, mac = match.group(1), match.group(2)
            if "incomplete" in line.lower():
                continue
            parts = mac.split(":")
            if len(parts) == 6:
                mac = ":".join(p.zfill(2) for p in parts).lower()
            result[ip_str] = mac
    return result


def get_hostname(ip):
    """Return hostname for IP, or None. Uses reverse DNS / mDNS."""
    try:
        name, _, _ = socket.gethostbyaddr(str(ip))
        return name
    except (socket.herror, socket.gaierror, OSError):
        return None


def check_port(ip, port, timeout=0.5):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((str(ip), port))
        s.close()
        return True
    except Exception:
        return False


def scan_ports(ip):
    """Return sorted list of open port numbers."""
    open_ports = []
    for port in COMMON_PORTS:
        if check_port(ip, port):
            open_ports.append(port)
    return sorted(open_ports)


def main():
    if len(sys.argv) < 2:
        print("Usage: lan_scan.py <CIDR> [--ports]")
        print("  default: IP, MAC, hostname")
        print("  --ports: IP, MAC, open ports")
        sys.exit(1)
    args = [a for a in sys.argv[1:] if a != "--ports"]
    do_ports = "--ports" in sys.argv
    if not args:
        print("Usage: lan_scan.py <CIDR> [--ports]")
        sys.exit(1)
    try:
        net = ipaddress.IPv4Network(args[0], strict=False)
    except ValueError as e:
        print(f"Invalid CIDR: {e}")
        sys.exit(1)
    hosts = list(net.hosts())
    if not hosts:
        print("No hosts in subnet")
        return
    print(f"Scanning {net} ({len(hosts)} hosts)" + (" — port scan" if do_ports else "") + "...")
    print()
    found = []
    with ThreadPoolExecutor(max_workers=60) as ex:
        futures = {ex.submit(ping, ip): ip for ip in hosts}
        for f in as_completed(futures):
            if f.result():
                found.append(futures[f])
    if not found:
        print("No hosts responded.")
        return
    # Give the kernel a moment to fill the ARP table after pings
    time.sleep(1)
    arp = get_arp_table()
    if do_ports:
        results = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            port_futures = {ex.submit(scan_ports, ip): ip for ip in found}
            for f in as_completed(port_futures):
                ip = port_futures[f]
                try:
                    open_ports = f.result()
                except Exception:
                    open_ports = []
                mac = arp.get(str(ip), "")
                results.append((ip, mac, open_ports))
        results.sort(key=lambda x: x[0])
        print(f"{'IP':<16} {'MAC':<18} {'Open ports'}")
        print("-" * 60)
        for ip, mac, open_ports in results:
            ports_str = ", ".join(str(p) for p in open_ports) if open_ports else "—"
            print(f"{str(ip):<16} {mac:<18} {ports_str}")
    else:
        results = []
        with ThreadPoolExecutor(max_workers=30) as ex:
            hostname_futures = {ex.submit(get_hostname, ip): ip for ip in found}
            for f in as_completed(hostname_futures):
                ip = hostname_futures[f]
                try:
                    hostname = f.result(timeout=HOSTNAME_TIMEOUT)
                except Exception:
                    hostname = None
                mac = arp.get(str(ip), "")
                results.append((ip, mac, hostname or "—"))
        results.sort(key=lambda x: x[0])
        print(f"{'IP':<16} {'MAC':<18} {'Hostname'}")
        print("-" * 60)
        for ip, mac, hostname in results:
            print(f"{str(ip):<16} {mac:<18} {hostname}")
    print()
    print(f"Done. {len(found)} host(s) responded.")


if __name__ == "__main__":
    main()
