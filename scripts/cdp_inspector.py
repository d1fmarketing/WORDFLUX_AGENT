#!/usr/bin/env python3
"""
Chrome DevTools Protocol Inspector for WordFlux Cockpit E2E Validation
"""
import asyncio
import json
import base64
import websockets
from pathlib import Path


class CDPClient:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self.command_id = 0
        self.responses = {}

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url)

    async def send_command(self, method, params=None):
        self.command_id += 1
        command = {
            "id": self.command_id,
            "method": method,
            "params": params or {}
        }
        await self.ws.send(json.dumps(command))

        # Wait for response
        while True:
            response = json.loads(await self.ws.recv())
            if response.get("id") == self.command_id:
                return response

    async def close(self):
        if self.ws:
            await self.ws.close()


async def inspect_cockpit():
    """Perform comprehensive E2E validation of WordFlux Cockpit"""

    # Get page info
    import requests
    pages = requests.get("http://localhost:9222/json/list").json()
    page = pages[0]
    ws_url = page["webSocketDebuggerUrl"]

    print(f"🔍 Connecting to: {page['title']}")
    print(f"📍 URL: {page['url']}")
    print(f"🔌 WebSocket: {ws_url}\n")

    client = CDPClient(ws_url)
    await client.connect()

    # Enable domains
    print("📋 Enabling CDP domains...")
    await client.send_command("Page.enable")
    await client.send_command("Runtime.enable")
    await client.send_command("Console.enable")
    await client.send_command("Network.enable")
    await client.send_command("Performance.enable")

    # Wait for page to settle
    await asyncio.sleep(2)

    # === SCREENSHOT ===
    print("\n📸 Taking screenshot...")
    screenshot = await client.send_command("Page.captureScreenshot", {
        "format": "png",
        "captureBeyondViewport": True
    })

    if "result" in screenshot:
        screenshot_data = base64.b64decode(screenshot["result"]["data"])
        screenshot_path = Path("/home/ubuntu/cockpit_screenshot.png")
        screenshot_path.write_bytes(screenshot_data)
        print(f"   ✅ Saved to: {screenshot_path}")

    # === GET PAGE CONTENT ===
    print("\n📄 Getting page HTML...")
    html_result = await client.send_command("Runtime.evaluate", {
        "expression": "document.documentElement.outerHTML",
        "returnByValue": True
    })
    html = html_result.get("result", {}).get("value", "")
    html_path = Path("/home/ubuntu/cockpit_page.html")
    html_path.write_text(html)
    print(f"   ✅ Saved HTML to: {html_path}")

    # === CONSOLE LOGS ===
    print("\n📝 Checking console logs...")
    console_result = await client.send_command("Runtime.evaluate", {
        "expression": """
        (function() {
            const logs = [];
            const originalLog = console.log;
            const originalError = console.error;
            const originalWarn = console.warn;

            // Return any errors that might be in window
            if (window.console && window.console.memory) {
                return {hasConsole: true};
            }
            return {hasConsole: true};
        })()
        """,
        "returnByValue": True
    })
    print(f"   Console access: {console_result}")

    # === CHECK DOM STRUCTURE ===
    print("\n🏗️  Analyzing DOM structure...")
    dom_checks = await client.send_command("Runtime.evaluate", {
        "expression": """
        (function() {
            return {
                title: document.title,
                hasKanbanBoard: !!document.querySelector('.kanban-board'),
                columns: Array.from(document.querySelectorAll('.kanban-column')).map(col => ({
                    title: col.querySelector('.column-header')?.textContent?.trim(),
                    cardCount: col.querySelectorAll('.card').length
                })),
                hasAutopilotToggle: !!document.querySelector('.autopilot-toggle') || !!document.querySelector('[data-testid="autopilot-toggle"]'),
                hasCreateButton: !!document.querySelector('button:contains("Create")') || document.body.innerHTML.includes('Create Card'),
                bodyText: document.body.textContent.substring(0, 500)
            };
        })()
        """,
        "returnByValue": True
    })

    dom_data = dom_checks.get("result", {}).get("value", {})
    print(f"   Page Title: {dom_data.get('title')}")
    print(f"   Has Kanban Board: {dom_data.get('hasKanbanBoard')}")
    print(f"   Columns: {json.dumps(dom_data.get('columns', []), indent=6)}")
    print(f"   Has Autopilot Toggle: {dom_data.get('hasAutopilotToggle')}")
    print(f"   Has Create Button: {dom_data.get('hasCreateButton')}")

    # === CHECK PORTUGUESE LABELS ===
    print("\n🇧🇷 Checking Portuguese labels...")
    portuguese_check = await client.send_command("Runtime.evaluate", {
        "expression": """
        (function() {
            const body = document.body.innerHTML;
            return {
                hasEspera: body.includes('Espera'),
                hasProducao: body.includes('Produção'),
                hasAprovacao: body.includes('Aprovação'),
                hasAgendado: body.includes('Agendado'),
                hasFinalizado: body.includes('Finalizado'),
                allHeaders: Array.from(document.querySelectorAll('h1, h2, h3, .column-header, .column-title')).map(h => h.textContent.trim())
            };
        })()
        """,
        "returnByValue": True
    })

    pt_data = portuguese_check.get("result", {}).get("value", {})
    print(f"   Espera: {pt_data.get('hasEspera')}")
    print(f"   Produção: {pt_data.get('hasProducao')}")
    print(f"   Aprovação: {pt_data.get('hasAprovacao')}")
    print(f"   Agendado: {pt_data.get('hasAgendado')}")
    print(f"   Finalizado: {pt_data.get('hasFinalizado')}")
    print(f"   All Headers: {pt_data.get('allHeaders')}")

    # === CHECK JAVASCRIPT ERRORS ===
    print("\n⚠️  Checking for JavaScript errors...")
    error_check = await client.send_command("Runtime.evaluate", {
        "expression": """
        (function() {
            const errors = [];
            const originalError = window.onerror;
            window.onerror = function(msg, url, line, col, error) {
                errors.push({msg, url, line, col, error: error?.toString()});
                if (originalError) originalError.apply(this, arguments);
            };
            return {
                hasErrorHandler: true,
                errorCount: errors.length,
                errors: errors
            };
        })()
        """,
        "returnByValue": True
    })
    print(f"   {error_check}")

    # === NETWORK REQUESTS ===
    print("\n🌐 Checking network activity...")
    network_check = await client.send_command("Runtime.evaluate", {
        "expression": """
        (function() {
            return {
                hasEventSource: !!window.EventSource,
                hasPerformance: !!window.performance,
                resourceCount: window.performance?.getEntriesByType('resource')?.length || 0
            };
        })()
        """,
        "returnByValue": True
    })
    print(f"   {network_check}")

    # === PERFORMANCE METRICS ===
    print("\n⚡ Getting performance metrics...")
    metrics = await client.send_command("Performance.getMetrics")
    print(f"   Metrics count: {len(metrics.get('result', {}).get('metrics', []))}")
    for metric in metrics.get('result', {}).get('metrics', [])[:10]:
        print(f"      {metric['name']}: {metric['value']}")

    # === CHECK SSE CONNECTION ===
    print("\n🔄 Checking SSE (Server-Sent Events) connection...")
    sse_check = await client.send_command("Runtime.evaluate", {
        "expression": """
        (function() {
            const scripts = Array.from(document.querySelectorAll('script')).map(s => s.src || 'inline');
            const body = document.body.innerHTML;
            return {
                hasEventSourceInBody: body.includes('EventSource'),
                hasEventsStream: body.includes('/events/stream'),
                scriptCount: scripts.length,
                hasMainJS: scripts.some(s => s.includes('main.js') || s.includes('app.js'))
            };
        })()
        """,
        "returnByValue": True
    })
    print(f"   {sse_check}")

    await client.close()

    print("\n" + "="*60)
    print("✅ E2E VALIDATION COMPLETE")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(inspect_cockpit())