"""Read-only client for the Flashforge AD5X LAN HTTP API."""
import json
import urllib.error
import urllib.request


class FlashForgeError(RuntimeError):
    pass


class FlashForgeAD5X:
    def __init__(self, host: str, serial: str, check_code: str, port: int = 8898, timeout: float = 5):
        self.host = host
        self.serial = serial
        self.check_code = check_code
        self.port = port
        self.timeout = timeout

    def get_detail(self) -> dict:
        if not all((self.host, self.serial, self.check_code)):
            raise FlashForgeError("Set PRINTER_HOST, PRINTER_SERIAL and PRINTER_CHECK_CODE.")
        url = f"http://{self.host}:{self.port}/detail"
        body = json.dumps({"serialNumber": self.serial, "checkCode": self.check_code}).encode()
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FlashForgeError(f"AD5X API request failed: {exc}") from exc
        if payload.get("code") != 0 or not isinstance(payload.get("detail"), dict):
            raise FlashForgeError(str(payload.get("message", "Unexpected AD5X API response")))
        detail = payload["detail"]
        # PID 38 is the AD5X. Do not silently label another Flashforge model as AD5X.
        pid = detail.get("pid")
        if pid is not None and str(pid).lower() not in ("38", "0x26"):
            raise FlashForgeError(f"Unexpected Flashforge product ID: {pid}")
        return detail


def normalize_detail(detail: dict, host: str) -> dict:
    """Expose only the telemetry needed by the hub; never forward credentials or camera URLs."""
    progress = detail.get("printProgress")
    try:
        progress = float(progress) if progress is not None else None
    except (TypeError, ValueError):
        progress = None
    if progress is not None and not 0 <= progress <= 1:
        progress = None

    def number(key):
        try:
            value = detail.get(key)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def integer(key):
        try:
            value = detail.get(key)
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    camera_url = str(detail.get("cameraStreamUrl") or "").strip()
    return {
        "type": "status",
        "printer_host": host,
        "printer_reachable": True,
        "printer_name": str(detail.get("name") or "Flashforge AD5X"),
        "printer_status": str(detail.get("status") or "unknown"),
        "firmware": str(detail.get("firmwareVersion") or ""),
        "progress": progress,
        "file_name": str(detail.get("printFileName") or ""),
        "nozzle_temp": number("rightTemp"),
        "nozzle_target": number("rightTargetTemp"),
        "bed_temp": number("platTemp"),
        "bed_target": number("platTargetTemp"),
        "layer": integer("printLayer"),
        "total_layers": integer("targetPrintLayer"),
        "remaining_seconds": number("estimatedTime"),
        "camera_available": bool(camera_url),
        "agent_version": "0.2.0",
    }
