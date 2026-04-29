"""
VPN presence check via ipinfo.io.

Called before every scrape run. If VPN is not detected, execution is
blocked and an alert is logged/emailed.

IVPN detection strategy:
  1. Check 'org' field for IVPN / common VPN provider strings.
  2. Optionally verify the exit country matches an expected value.
     (e.g. expected_country='SE' if routing through Sweden)

Usage:
    from utils.vpn import require_vpn

    ip = require_vpn()          # raises RuntimeError if no VPN
"""

from __future__ import annotations

import requests


# Known VPN / hosting provider strings that appear in ipinfo.io 'org' field.
# IVPN routes through M247 Europe SRL (AS9009) as their transit provider —
# ipinfo.io reports M247 rather than IVPN, so both must be listed.
_VPN_ORG_INDICATORS = [
    "ivpn", "mullvad", "nordvpn", "expressvpn", "protonvpn",
    "cyberghost", "perfect privacy", "azirevpn", "vpn", "proxy",
    "m247", "as9009",
]


def check_vpn(expected_country: str | None = None) -> tuple[bool, str]:
    """
    Query ipinfo.io and heuristically determine if a VPN is active.

    Args:
        expected_country: ISO-3166-1 alpha-2 code (e.g. 'SE') of the expected
                          VPN exit country. If provided, the IP must match.

    Returns:
        (is_vpn_active, current_ip_address)

    Raises:
        RuntimeError: If ipinfo.io is unreachable.
    """
    try:
        resp = requests.get("https://ipinfo.io/json", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"VPN check failed — cannot reach ipinfo.io: {exc}") from exc

    ip      = data.get("ip", "unknown")
    org     = data.get("org", "").lower()
    country = data.get("country", "")

    # Match on org name
    vpn_by_org = any(indicator in org for indicator in _VPN_ORG_INDICATORS)

    # Match on expected exit country
    vpn_by_country = bool(expected_country and country == expected_country)

    is_active = vpn_by_org or vpn_by_country
    return is_active, ip


def require_vpn(expected_country: str | None = None) -> str:
    """
    Assert VPN is active and (if given) on the correct exit country.

    Returns:
        The current IP address (for logging to scrape_runs).

    Raises:
        RuntimeError: If no VPN is detected, or if the exit country doesn't
                      match expected_country.
    """
    try:
        resp = requests.get("https://ipinfo.io/json", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"VPN check failed — cannot reach ipinfo.io: {exc}") from exc

    ip      = data.get("ip", "unknown")
    org     = data.get("org", "").lower()
    country = data.get("country", "")

    vpn_detected = any(indicator in org for indicator in _VPN_ORG_INDICATORS)

    if not vpn_detected:
        raise RuntimeError(
            f"VPN not detected! Current IP: {ip}. "
            "Activate IVPN before running scrapers."
        )
    if expected_country and country != expected_country:
        raise RuntimeError(
            f"VPN exit country mismatch: expected {expected_country}, got {country} "
            f"(IP: {ip}). Switch IVPN exit node to {expected_country} before running."
        )
    return ip
