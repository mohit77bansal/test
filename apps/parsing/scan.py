"""Virus scan seam: `clamd` (INSTREAM over TCP) or `none`."""
import socket
import struct

from django.conf import settings


def scan_bytes(content: bytes) -> str:
    """Returns 'CLEAN', 'INFECTED' or 'SKIPPED'."""
    if settings.VIRUS_SCAN_ENGINE == "none":
        return "SKIPPED"
    with socket.create_connection((settings.CLAMD_HOST, settings.CLAMD_PORT), timeout=30) as s:
        s.sendall(b"zINSTREAM\0")
        for i in range(0, len(content), 8192):
            chunk = content[i : i + 8192]
            s.sendall(struct.pack("!L", len(chunk)) + chunk)
        s.sendall(struct.pack("!L", 0))
        reply = s.recv(4096).decode(errors="replace")
    if reply.strip("\0").endswith("OK"):
        return "CLEAN"
    if "FOUND" in reply:
        return "INFECTED"
    raise RuntimeError(f"clamd error: {reply}")
