"""Browser acceptance against a running app with the development reloader off.

Run: python tests/browser/check_crime_only.py http://127.0.0.1:8051
Requires local Playwright and Edge. Uses the real snapshot and UI controls.
"""

import re
import sys
import time

from playwright.sync_api import expect, sync_playwright


def run(base_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.set_default_timeout(60000)
        errors, failed_callbacks, toggles = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text)
                if message.type == "error" and "Map error" in message.text else None)

        def response_received(response):
            if response.url.endswith("/_dash-update-component") and response.status >= 400:
                failed_callbacks.append(response.status)

        def request_sent(request):
            if not request.url.endswith("/_dash-update-component"):
                return
            body = request.post_data_json
            if "crime-map-region-toggle.data" in body.get("changedPropIds", []) and any(
                item["id"] == "crime-map-region-toggle" and item.get("value")
                for item in body["inputs"]
            ):
                toggles.append(body)

        page.on("response", response_received)
        page.on("request", request_sent)
        graph = page.locator("#crime-map-figure .js-plotly-plot")

        def wait_map(layer):
            page.wait_for_function("""layer => {
                const graph = document.querySelector('#crime-map-figure .js-plotly-plot');
                if (!graph || !graph.data || !graph.data.length) return false;
                const types = graph.data.map(trace => trace.type);
                return layer === 'points' ? types.every(t => t === 'scattermap') :
                    layer === 'choropleth' ? types.every(t => t === 'choroplethmap') :
                    types.includes('scattermap') && types.includes('choroplethmap');
            }""", arg=layer)
            expect(page.locator("#crime-map-figure .maplibregl-canvas")).to_be_visible()
            expect(page.locator("#crime-map-point-window-label")).to_contain_text("Map points:")

        for route in ("/", "/crime-v1-1", "/calls", "/crime"):
            response = page.goto(base_url + route, wait_until="domcontentloaded")
            assert response.status == 200
            expect(page.get_by_role("heading", name="Seattle Crime Dashboard", exact=True)).to_be_visible()
            expect(page.locator("#crime-v11-ranking-body tbody tr")).to_have_count(10)
            expect(page.locator("#crime-v11-response-body")).to_contain_text("min")
            expect(page.locator("#map-figure, #daily-figure, #scatter-figure, .landing-hero, .page-nav")).to_have_count(0)
            wait_map("choropleth")
            print(f"PASS: {route} renders canonical crime dashboard", flush=True)

        page.locator("#crime-daily-figure .rangeselector .button").filter(has_text="1Y").click()
        expect(page.locator("#crime-analysis-period-duration")).to_have_text("(Latest year)")
        expect(page.locator("#crime-map-point-window-label")).to_contain_text("Map points:")
        dates = re.search(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})",
                          page.locator("#crime-map-point-window-label").inner_text())
        # Wait for the map callback, which may finish after the period label.
        end = dates[2]
        start = f"{int(end[:4]) - 1}{end[4:]}"
        expect(page.locator("#crime-map-point-window-label")).to_contain_text(f"{start} to {end}")
        print("PASS: one-year analysis and map window", flush=True)

        for label, layer in (("Points", "points"), ("Neighborhoods", "choropleth"), ("Both", "both")):
            previous_graph = graph.element_handle()
            page.locator("#crime-map-layer").get_by_text(label, exact=True).click()
            wait_map(layer)
            assert previous_graph.evaluate("element => !element.isConnected")
            print(f"PASS: {label} with hard map remount", flush=True)

        expect(page.locator("#crime-v11-response-body")).to_contain_text("min")
        for control in ("crime-v11-response-priority", "crime-v11-ranking-priority"):
            for label in ("Priority 1 only", "Priority 1–2", "Priority 1–3"):
                with page.expect_response(lambda r: r.url.endswith("/_dash-update-component")) as result:
                    page.locator(f"#{control}").get_by_text(label, exact=True).click()
                assert result.value.status == 200
        page.locator("#crime-v11-ranking-min-events").fill("1")
        page.locator("#crime-v11-ranking-min-events").press("Tab")
        print("PASS: response KPI, priority selectors, minimum response events", flush=True)

        for label, column in (("Call Volume", 4), ("Crime Volume", 5), ("Response Time", 3)):
            page.locator("#crime-v11-ranking-metric").get_by_text(label, exact=True).click()
            expect(page.locator(f"#crime-v11-ranking-body tbody tr:first-child td:nth-child({column})")).to_have_class("crime-v11-ranked-metric")
            expect(page.locator("#crime-v11-ranking-body tbody tr")).to_have_count(10)
            print(f"PASS: ranking {label}", flush=True)

        page.locator("#crime-v11-ranking-expand").click()
        expect(page.locator("#crime-v11-ranking-panel")).to_have_class(re.compile("fullscreen-overlay"))
        page.wait_for_function("document.querySelectorAll('#crime-v11-ranking-body tbody tr').length > 10")
        page.locator("#crime-v11-ranking-expand").click()
        expect(page.locator("#crime-v11-ranking-body tbody tr")).to_have_count(10)
        print("PASS: ranking fullscreen expands and restores rows", flush=True)

        page.locator("#crime-expand-map-button").click()
        expect(page.locator("#crime-fullscreen-overlay")).to_have_class("fullscreen-overlay")
        expect(page.locator("#crime-fullscreen-figure .maplibregl-canvas")).to_be_visible()
        expect(page.locator("#crime-fullscreen-title")).to_contain_text("Map view")
        page.locator("#crime-close-fullscreen-button").click()
        expect(page.locator("#crime-fullscreen-overlay")).to_have_class(re.compile("hidden"))
        print("PASS: map fullscreen opens and closes", flush=True)

        # Click actual rendered polygons. The canvas center can be water, so try
        # nearby positions until Plotly reports a neighborhood to the bridge.
        page.locator("#crime-map-layer").get_by_text("Neighborhoods", exact=True).click()
        wait_map("choropleth")
        canvas = page.locator("#crime-map-figure .maplibregl-canvas")
        canvas.scroll_into_view_if_needed()
        before = page.locator("#crime-neighborhood-filter").inner_text()
        toggles.clear()
        for x, y in ((.5, .5), (.45, .55), (.4, .6), (.55, .35), (.4, .35)):
            bounds = canvas.bounding_box()
            canvas.click(position={"x": bounds["width"] * x, "y": bounds["height"] * y}, modifiers=["Control"])
            deadline = time.monotonic() + 3
            while not toggles and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            if toggles:
                break
        assert toggles, "Ctrl-click did not emit a neighborhood toggle"
        expect(page.locator("#crime-neighborhood-filter")).not_to_have_text(before)
        print("PASS: Ctrl-click changes global neighborhood selection", flush=True)
        assert not failed_callbacks, failed_callbacks
        assert not errors, errors
        assert "Map error." not in page.locator("body").inner_text()
        print("PASS: no failed Dash callbacks, browser exceptions, or Map error.", flush=True)
        browser.close()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8050")
