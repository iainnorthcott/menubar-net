import socket
import urllib.request


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "unknown"
    return local_ip


def get_public_ip():
    url = "https://api.ipify.org"
    with urllib.request.urlopen(url) as response:
        public_ip = response.read().decode("utf-8")
    return public_ip


if __name__ == "__main__":
    print("Local IP:", get_local_ip())
    print("Public IP:", get_public_ip())