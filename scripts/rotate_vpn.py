#!/usr/bin/env python3
"""
Standalone VPN rotation script — intended for system cron (every 4 hours).

Rotates between London (gb.wg.ivpn.net) and Manchester (gb-man.wg.ivpn.net)
IVPN UK exit nodes to keep a fresh IP for all scrapers.

Crontab entry (runs at 07:13, 11:13, 15:13, 19:13, 23:13):
    13 7,11,15,19,23 * * * /usr/bin/env python3 /path/to/scripts/rotate_vpn.py >> /tmp/rotate_vpn.log 2>&1

Usage:
    python3 scripts/rotate_vpn.py
"""
import sys
import os

# Allow running from any working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from utils.ivpn import log_active_server, rotate_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if __name__ == "__main__":
    log_active_server()
    try:
        new_server = rotate_server(wait_s=8)
        print(f"OK: rotated to {new_server}")
        sys.exit(0)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
