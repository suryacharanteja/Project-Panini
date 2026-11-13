"""Load the actual pinned Playwright Android transport without installing anything on a phone."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from backend.hands.android_driver import AndroidDriver

    driver = AndroidDriver()
    try:
        devices = await driver.call("devices", consent=True)
        assert isinstance(devices, list)
        assert all(isinstance(d.get("serial"), str) and isinstance(d.get("model"), str) for d in devices)
        print(
            json.dumps(
                {
                    "transport": "pinned Playwright Android",
                    "enumerated_devices": len(devices),
                    "device_actions": 0,
                    "physical_qualification": False,
                }
            )
        )
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
