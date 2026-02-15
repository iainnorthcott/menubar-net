import psutil
from psutil import AccessDenied

def list_connections():
    results = []
    try:
        conns = psutil.net_connections(kind="inet")
    except AccessDenied:
        # If the global call is blocked entirely, just return empty
        return results

    for c in conns:
        # Some individual entries can still cause AccessDenied
        try:
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
            status = c.status
            pid = c.pid
        except AccessDenied:
            continue

        if not raddr:
            continue

        results.append({
            "local": laddr,
            "remote": raddr,
            "status": status,
            "pid": pid,
        })

    return results

if __name__ == "__main__":
    try:
        conns = list_connections()
        for conn in conns:
            print(
                f"Local: {conn['local']:<22} "
                f"Remote: {conn['remote']:<22} "
                f"Status: {conn['status']:<13} "
                f"PID: {conn['pid']}"
            )
        if not conns:
            print("No connections found or access denied for all.")
    except Exception as e:
        print("Error:", e)