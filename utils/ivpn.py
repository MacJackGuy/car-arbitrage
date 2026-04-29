"""
IVPN server rotation utilities.

Rotates between UK nodes for rate-limit avoidance (eBay etc.) and switches
to a Singapore exit node for SG scrapers that are Cloudflare-blocked by UK IPs.

Requires the `ivpn` CLI (IVPN desktop client must be installed and running).
"""
from __future__ import annotations

import asyncio
import logging
import subprocess

log = logging.getLogger(__name__)

_LONDON      = "gb.wg.ivpn.net"
_MANCHESTER  = "gb-man.wg.ivpn.net"
_SINGAPORE   = "sg.wg.ivpn.net"
_UK_SERVERS  = [_LONDON, _MANCHESTER]


def get_active_server() -> str | None:
    """
    Return the hostname of the active IVPN server, or None if not connected.

    Parses `ivpn status` output — the connected server appears on the line
    immediately after 'CONNECTED', e.g.:
        VPN                    : CONNECTED
                                 gb-man.wg.ivpn.net [gb-man1.wg.ivpn.net], Manchester (GB)...
    """
    try:
        out = subprocess.check_output(["ivpn", "status"], text=True, timeout=10)
        found_connected = False
        for line in out.splitlines():
            stripped = line.strip()
            if "CONNECTED" in stripped:
                found_connected = True
                continue
            if found_connected and stripped:
                # First non-empty line after CONNECTED contains the server hostname
                hostname = stripped.split()[0]
                return hostname
        return None
    except Exception as exc:
        log.warning("ivpn status failed: %s", exc)
        return None


def get_alternate_server(current: str | None) -> str:
    """Return the other UK server. Defaults to London if current is unknown."""
    if current and _MANCHESTER in current:
        return _LONDON
    return _MANCHESTER


def rotate_server(wait_s: int = 8) -> str:
    """
    Switch to the alternate UK IVPN server and wait for the connection.

    Returns:
        Hostname of the new server.

    Raises:
        RuntimeError: If the `ivpn connect` command fails.
    """
    current   = get_active_server()
    alternate = get_alternate_server(current)
    city      = "London" if alternate == _LONDON else "Manchester"

    log.info("[ivpn] Rotating: %s → %s (%s)", current or "unknown", alternate, city)
    try:
        subprocess.check_call(["ivpn", "connect", alternate], timeout=30)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ivpn connect failed: {exc}") from exc

    import time
    time.sleep(wait_s)
    log.info("[ivpn] Connected to %s — waited %ds", alternate, wait_s)
    return alternate


async def rotate_server_async(wait_s: int = 8) -> str:
    """Async wrapper for rotate_server — runs subprocess in executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: rotate_server(wait_s))


def switch_to_server(hostname: str, wait_s: int = 12) -> str:
    """
    Connect to a specific IVPN server by hostname and wait for the tunnel.

    Returns:
        The hostname connected to.

    Raises:
        RuntimeError: If `ivpn connect` fails.
    """
    import time
    current = get_active_server()
    if current and hostname in current:
        log.info("[ivpn] Already on %s — no switch needed", hostname)
        return hostname

    log.info("[ivpn] Switching: %s → %s", current or "unknown", hostname)
    try:
        subprocess.check_call(["ivpn", "connect", hostname], timeout=30)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ivpn connect {hostname} failed: {exc}") from exc

    time.sleep(wait_s)
    log.info("[ivpn] Connected to %s — waited %ds", hostname, wait_s)
    return hostname


async def switch_to_server_async(hostname: str, wait_s: int = 12) -> str:
    """Async wrapper for switch_to_server."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: switch_to_server(hostname, wait_s))


def disconnect(wait_s: int = 5) -> None:
    """Disconnect IVPN entirely (no VPN tunnel)."""
    import time
    log.info("[ivpn] Disconnecting VPN...")
    try:
        subprocess.check_call(["ivpn", "disconnect"], timeout=15)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ivpn disconnect failed: {exc}") from exc
    time.sleep(wait_s)
    log.info("[ivpn] Disconnected — no VPN active")


async def disconnect_async(wait_s: int = 5) -> None:
    """Async wrapper for disconnect."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: disconnect(wait_s))


def switch_to_singapore(wait_s: int = 12) -> str:
    """Connect to the Singapore IVPN exit node."""
    return switch_to_server(_SINGAPORE, wait_s)


def switch_to_uk(wait_s: int = 12) -> str:
    """Connect to the London UK IVPN exit node."""
    return switch_to_server(_LONDON, wait_s)


async def switch_to_singapore_async(wait_s: int = 12) -> str:
    """Async wrapper for switch_to_singapore."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: switch_to_singapore(wait_s))


async def switch_to_uk_async(wait_s: int = 12) -> str:
    """Async wrapper for switch_to_uk."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: switch_to_uk(wait_s))


def log_active_server() -> str | None:
    """Log and return the active IVPN server hostname."""
    server = get_active_server()
    if server:
        if _SINGAPORE in server:
            city = "Singapore"
        elif _MANCHESTER in server:
            city = "Manchester"
        else:
            city = "London"
        log.info("[ivpn] Active server: %s (%s)", server, city)
    else:
        log.warning("[ivpn] Could not determine active server")
    return server
