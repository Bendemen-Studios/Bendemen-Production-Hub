import asyncio
import json
import os

from dotenv import load_dotenv
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from flashforge import FlashForgeAD5X, FlashForgeError, normalize_detail

load_dotenv()
HUB_WS_URL = os.getenv("HUB_WS_URL", "")
AGENT_ID = os.getenv("HUB_AGENT_ID", "ad5x-home")
AGENT_TOKEN = os.getenv("HUB_AGENT_TOKEN", "")
PRINTER_HOST = os.getenv("PRINTER_HOST", "")
PRINTER_SERIAL = os.getenv("PRINTER_SERIAL", "")
PRINTER_CHECK_CODE = os.getenv("PRINTER_CHECK_CODE", "")
PRINTER_PORT = int(os.getenv("PRINTER_API_PORT", "8898"))
INTERVAL = max(3, int(os.getenv("CHECK_INTERVAL_SECONDS", "5")))
VERSION = "0.2.0"

printer = FlashForgeAD5X(PRINTER_HOST, PRINTER_SERIAL, PRINTER_CHECK_CODE, PRINTER_PORT)


async def collect_status():
    try:
        detail = await asyncio.to_thread(printer.get_detail)
        return normalize_detail(detail, PRINTER_HOST)
    except FlashForgeError as exc:
        return {
            "type": "status",
            "printer_host": PRINTER_HOST,
            "printer_reachable": False,
            "api_error": str(exc)[:160],
            "agent_version": VERSION,
        }


async def session():
    async with connect(HUB_WS_URL, ping_interval=20, ping_timeout=20, max_size=1_000_000) as ws:
        await ws.send(json.dumps({"type": "auth", "agent_id": AGENT_ID, "token": AGENT_TOKEN}))
        while True:
            payload = await collect_status()
            payload["agent_version"] = VERSION
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
