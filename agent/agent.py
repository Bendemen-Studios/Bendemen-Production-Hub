import asyncio
import json
import os
import socket
from datetime import datetime, timezone

from dotenv import load_dotenv
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

load_dotenv()
HUB_WS_URL = os.getenv("HUB_WS_URL", "")
AGENT_ID = os.getenv("HUB_AGENT_ID", "ad5x-home")
AGENT_TOKEN = os.getenv("HUB_AGENT_TOKEN", "")
PRINTER_HOST = os.getenv("PRINTER_HOST", "")
PRINTER_PORT = int(os.getenv("PRINTER_PORT", "8899"))
INTERVAL = max(3, int(os.getenv("CHECK_INTERVAL_SECONDS", "10")))
CAMERA_CONFIGURED = os.getenv("CAMERA_CONFIGURED", "false").lower() == "true"
VERSION = "0.1.0"


def printer_reachable() -> bool:
    if not PRINTER_HOST:
        return False
    try:
        with socket.create_connection((PRINTER_HOST, PRINTER_PORT), timeout=3):
            return True
    except OSError:
        return False


async def session():
    async with connect(HUB_WS_URL, ping_interval=20, ping_timeout=20, max_size=1_000_000) as ws:
        await ws.send(json.dumps({"type": "auth", "agent_id": AGENT_ID, "token": AGENT_TOKEN}))
        while True:
            payload = {
                "type": "status",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "printer_host": PRINTER_HOST,
                "printer_port": PRINTER_PORT,
                "printer_reachable": printer_reachable(),
                "camera_configured": CAMERA_CONFIGURED,
                "agent_version": VERSION,
            }
            await ws.send(json.dumps(payload))
            await asyncio.sleep(INTERVAL)


async def main():
    if not HUB_WS_URL.startswith("wss://"):
        raise SystemExit("HUB_WS_URL must use wss:// in production.")
    if not HUB_AGENT_TOKEN:
        raise SystemExit("Set HUB_AGENT_TOKEN in agent/.env.")
    while True:
        try:
            print(f"Connecting to {HUB_WS_URL}")
            await session()
        except (OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
            print(f"Connection lost: {exc}; retrying in 5 seconds.")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
