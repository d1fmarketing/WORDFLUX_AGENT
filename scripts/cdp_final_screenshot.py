#!/usr/bin/env python3
"""Take final screenshot after card creation"""
import asyncio
import json
import base64
import websockets
from pathlib import Path


async def capture_final():
    import requests
    pages = requests.get("http://localhost:9222/json/list").json()
    ws_url = pages[0]["webSocketDebuggerUrl"]

    ws = await websockets.connect(ws_url)
    cmd_id = 0

    async def send_cmd(method, params=None):
        nonlocal cmd_id
        cmd_id += 1
        await ws.send(json.dumps({"id": cmd_id, "method": method, "params": params or {}}))
        response = json.loads(await ws.recv())
        return response

    await send_cmd("Page.enable")

    # Reload page
    print("Reloading page...")
    await send_cmd("Page.reload", {"ignoreCache": True})
    await asyncio.sleep(3)

    # Capture screenshot
    print("Capturing final screenshot...")
    screenshot = await send_cmd("Page.captureScreenshot", {"format": "png"})
    if "result" in screenshot:
        path = Path("/home/ubuntu/cockpit_screenshot_final.png")
        path.write_bytes(base64.b64decode(screenshot["result"]["data"]))
        print(f"✅ Saved: {path}")

    # Get updated card count
    card_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const columns = Array.from(document.querySelectorAll('.col'));
            return columns.map(col => {
                const header = col.querySelector('h3');
                const cards = col.querySelectorAll('.cardx');
                return {
                    title: header?.textContent?.trim(),
                    count: cards.length
                };
            });
        })()
        """,
        "returnByValue": True
    })

    print("\n📇 Card counts per column:")
    cards = card_check.get("result", {}).get("result", {}).get("value", [])
    for col in cards:
        print(f"   {col.get('title')}: {col.get('count')} card(s)")

    await ws.close()


if __name__ == "__main__":
    asyncio.run(capture_final())