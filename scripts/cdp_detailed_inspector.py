#!/usr/bin/env python3
"""
Enhanced Chrome DevTools Protocol Inspector for WordFlux Cockpit
"""
import asyncio
import json
import base64
import websockets
from pathlib import Path


async def inspect_cockpit_detailed():
    """Comprehensive E2E validation with detailed inspection"""

    import requests
    pages = requests.get("http://localhost:9222/json/list").json()
    page = pages[0]
    ws_url = page["webSocketDebuggerUrl"]

    print("="*80)
    print("🔍 WORDFLUX COCKPIT E2E VALIDATION REPORT")
    print("="*80)
    print(f"\n📍 Target: {page['url']}")
    print(f"📋 Page Title: {page['title']}")
    print(f"🔌 CDP WebSocket: {ws_url}\n")

    ws = await websockets.connect(ws_url)
    cmd_id = 0

    async def send_cmd(method, params=None):
        nonlocal cmd_id
        cmd_id += 1
        await ws.send(json.dumps({"id": cmd_id, "method": method, "params": params or {}}))
        while True:
            response = json.loads(await ws.recv())
            if response.get("id") == cmd_id:
                return response

    # Enable domains
    await send_cmd("Page.enable")
    await send_cmd("Runtime.enable")
    await send_cmd("Console.enable")
    await send_cmd("Network.enable")
    await send_cmd("DOM.enable")

    # Wait for page to settle
    await asyncio.sleep(2)

    # === SCREENSHOT ===
    print("📸 SCREENSHOT CAPTURE")
    print("-" * 80)
    screenshot = await send_cmd("Page.captureScreenshot", {"format": "png"})
    if "result" in screenshot:
        screenshot_path = Path("/home/ubuntu/cockpit_screenshot.png")
        screenshot_path.write_bytes(base64.b64decode(screenshot["result"]["data"]))
        print(f"✅ Screenshot saved: {screenshot_path}")

    # === GET DOCUMENT ===
    print("\n🌳 DOM TREE ANALYSIS")
    print("-" * 80)
    doc = await send_cmd("DOM.getDocument", {"depth": -1})
    print(f"✅ DOM retrieved: {doc.get('result', {}).get('root', {}).get('nodeName')}")

    # === DETAILED DOM INSPECTION ===
    print("\n🔍 DETAILED COMPONENT INSPECTION")
    print("-" * 80)

    # Check for Portuguese column headers
    columns_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        Array.from(document.querySelectorAll('.workflow-board .column-header, .workflow-board .column-title, [class*="column"]')).map(el => ({
            text: el.textContent.trim(),
            classes: el.className,
            tag: el.tagName
        }))
        """,
        "returnByValue": True
    })

    if "result" in columns_check and "result" in columns_check["result"]:
        columns = columns_check["result"]["result"].get("value", [])
        print(f"\n📊 Column Headers Found: {len(columns)}")
        for col in columns:
            print(f"   • {col.get('text')} ({col.get('tag')}.{col.get('classes')})")

    # Check autopilot toggle
    autopilot_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const toggle = document.querySelector('input[type="checkbox"]');
            const label = document.querySelector('label');
            return {
                hasToggle: !!toggle,
                isChecked: toggle?.checked,
                labelText: label?.textContent?.trim(),
                autopilotText: document.body.textContent.includes('Autopilot')
            };
        })()
        """,
        "returnByValue": True
    })

    autopilot_data = autopilot_check.get("result", {}).get("result", {}).get("value", {})
    print(f"\n🤖 AUTOPILOT CONTROL")
    print(f"   Toggle present: {autopilot_data.get('hasToggle')}")
    print(f"   Toggle checked: {autopilot_data.get('isChecked')}")
    print(f"   Label text: {autopilot_data.get('labelText')}")

    # Check for manual "Create" button (should NOT exist)
    create_button_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const buttons = Array.from(document.querySelectorAll('button'));
            const createButtons = buttons.filter(b =>
                b.textContent.toLowerCase().includes('create') ||
                b.textContent.toLowerCase().includes('criar')
            );
            return {
                totalButtons: buttons.length,
                createButtonCount: createButtons.length,
                createButtonTexts: createButtons.map(b => b.textContent.trim()),
                allButtonTexts: buttons.map(b => b.textContent.trim())
            };
        })()
        """,
        "returnByValue": True
    })

    button_data = create_button_check.get("result", {}).get("result", {}).get("value", {})
    print(f"\n🔘 BUTTON INVENTORY")
    print(f"   Total buttons: {button_data.get('totalButtons')}")
    print(f"   'Create' buttons: {button_data.get('createButtonCount')}")
    if button_data.get('createButtonCount', 0) > 0:
        print(f"   ⚠️  WARNING: Found manual create buttons (should not exist in agent-first mode):")
        for btn in button_data.get('createButtonTexts', []):
            print(f"      • {btn}")
    else:
        print(f"   ✅ No manual 'Create' buttons (agent-first requirement met)")

    # Check Portuguese labels specifically
    print(f"\n🇧🇷 PORTUGUESE LOCALIZATION")
    print("-" * 80)
    pt_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const body = document.body.textContent;
            return {
                espera: body.includes('ESPERA'),
                producao: body.includes('PRODUÇÃO'),
                aprovacao: body.includes('APROVAÇÃO'),
                agendado: body.includes('AGENDADO'),
                finalizado: body.includes('FINALIZADO'),
                allText: Array.from(document.querySelectorAll('.column-header, .column-title, h2, h3')).map(el => el.textContent.trim())
            };
        })()
        """,
        "returnByValue": True
    })

    pt_data = pt_check.get("result", {}).get("result", {}).get("value", {})
    print(f"   ESPERA: {'✅' if pt_data.get('espera') else '❌'}")
    print(f"   PRODUÇÃO: {'✅' if pt_data.get('producao') else '❌'}")
    print(f"   APROVAÇÃO: {'✅' if pt_data.get('aprovacao') else '❌'}")
    print(f"   AGENDADO: {'✅' if pt_data.get('agendado') else '❌'}")
    print(f"   FINALIZADO: {'✅' if pt_data.get('finalizado') else '❌'}")

    # Console errors
    print(f"\n⚠️  JAVASCRIPT CONSOLE")
    print("-" * 80)
    console_check = await send_cmd("Runtime.evaluate", {
        "expression": "window.performance.getEntriesByType('navigation')[0].toJSON()",
        "returnByValue": True
    })
    print(f"   Navigation timing available: {'✅' if 'result' in console_check else '❌'}")

    # Network resources
    print(f"\n🌐 NETWORK ANALYSIS")
    print("-" * 80)
    network_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const resources = performance.getEntriesByType('resource');
            const byType = {};
            resources.forEach(r => {
                const ext = r.name.split('.').pop().split('?')[0];
                byType[ext] = (byType[ext] || 0) + 1;
            });
            return {
                total: resources.length,
                byType: byType,
                hasSSE: resources.some(r => r.name.includes('/events/stream')),
                hasBoardState: resources.some(r => r.name.includes('/board/state'))
            };
        })()
        """,
        "returnByValue": True
    })

    net_data = network_check.get("result", {}).get("result", {}).get("value", {})
    print(f"   Total resources loaded: {net_data.get('total')}")
    print(f"   SSE stream endpoint: {'✅' if net_data.get('hasSSE') else '❌'}")
    print(f"   Board state endpoint: {'✅' if net_data.get('hasBoardState') else '❌'}")

    # Performance metrics
    print(f"\n⚡ PERFORMANCE METRICS")
    print("-" * 80)
    perf = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const nav = performance.getEntriesByType('navigation')[0];
            return {
                domContentLoaded: Math.round(nav.domContentLoadedEventEnd - nav.domContentLoadedEventStart),
                loadComplete: Math.round(nav.loadEventEnd - nav.loadEventStart),
                domInteractive: Math.round(nav.domInteractive),
                responseEnd: Math.round(nav.responseEnd)
            };
        })()
        """,
        "returnByValue": True
    })

    perf_data = perf.get("result", {}).get("result", {}).get("value", {})
    print(f"   DOM Interactive: {perf_data.get('domInteractive')}ms")
    print(f"   DOM Content Loaded: {perf_data.get('domContentLoaded')}ms")
    print(f"   Load Complete: {perf_data.get('loadComplete')}ms")

    # Card count and WIP limits
    print(f"\n📇 KANBAN CARD INVENTORY")
    print("-" * 80)
    card_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const columns = Array.from(document.querySelectorAll('.workflow-board > div'));
            return columns.map((col, idx) => {
                const header = col.querySelector('.column-header, h2, h3');
                const cards = col.querySelectorAll('.card, [class*="card"]');
                return {
                    index: idx,
                    title: header?.textContent?.trim(),
                    cardCount: cards.length,
                    cardTitles: Array.from(cards).slice(0, 3).map(c => c.textContent.trim().substring(0, 50))
                };
            });
        })()
        """,
        "returnByValue": True
    })

    card_data = card_check.get("result", {}).get("result", {}).get("value", [])
    for col in card_data:
        count = col.get('cardCount', 0)
        title = col.get('title', 'Unknown')
        print(f"   Column: {title} - {count} card(s)")
        if count > 0:
            for card_title in col.get('cardTitles', []):
                print(f"      • {card_title}...")

    # Connection status
    print(f"\n🔗 CONNECTION STATUS")
    print("-" * 80)
    conn_check = await send_cmd("Runtime.evaluate", {
        "expression": """
        (function() {
            const statusEl = document.querySelector('.connection-status, [class*="connected"], [class*="status"]');
            return {
                statusText: statusEl?.textContent?.trim(),
                hasConnected: document.body.textContent.includes('Connected'),
                hasDisconnected: document.body.textContent.includes('Disconnected')
            };
        })()
        """,
        "returnByValue": True
    })

    conn_data = conn_check.get("result", {}).get("result", {}).get("value", {})
    print(f"   Status: {conn_data.get('statusText')}")
    print(f"   Connected: {'✅' if conn_data.get('hasConnected') else '❌'}")

    await ws.close()

    print("\n" + "="*80)
    print("✅ E2E VALIDATION COMPLETE")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(inspect_cockpit_detailed())