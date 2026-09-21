"""Run against a staging app on port 8050: python tests/browser/check_range_debounce.py.

Requires local Playwright and Edge. Uses real mouse dragging and observes Dash
HTTP requests; it does not access Plotly's private layout or event internals.
"""
import io
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

from PIL import Image
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/dashboard"))


def run():
    with urllib.request.urlopen("http://127.0.0.1:8050/healthz", timeout=5) as response:
        assert response.status == 200
    print("PASS: staging /healthz HTTP 200", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.set_default_timeout(120000)
        commits = []
        for name, graph, label in [
            ("crime", "crime-daily-figure", "crime-map-point-window-label"),
        ]:
            store = f"{name}-daily-relayout-debounced-store"

            def observe(request):
                if not request.url.endswith("/_dash-update-component"):
                    return
                body = request.post_data_json
                if f"{store}.data" in body.get("changedPropIds", []):
                    value = next(item.get("value") for item in body["inputs"] if item["id"] == store)
                    if value is not None:  # Dynamic layout mounting may omit initial data.
                        commits.append({"time": time.monotonic(), "value": value, "output": body["output"]})

            page.on("request", observe)
            page.goto(f"http://127.0.0.1:8050/{name}", wait_until="domcontentloaded")
            expect(page.get_by_text("STAGING ENVIRONMENT", exact=True)).to_be_visible()
            buttons = page.locator(f"#{graph} .rangeselector .button")
            expect(buttons).to_have_count(4)
            expect(page.locator(f"#{label}")).to_contain_text("Map points:")

            def wait_for_commit():
                deadline = time.monotonic() + 15
                while not commits and time.monotonic() < deadline:
                    page.wait_for_timeout(100)
                assert commits, f"{name}: no settled range reached Python"
                return commits[0]["value"]

            def map_dates():
                return re.search(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})",
                                 page.locator(f"#{label}").inner_text()).groups()

            def wait_map_for_payload(payload):
                if payload.get("xaxis.autorange"):
                    end = map_dates()[1]
                    import pandas as pd
                    start = (pd.Timestamp(end) - pd.DateOffset(years=1)).date().isoformat()
                else:
                    values = payload.get("xaxis.range") or [payload["xaxis.range[0]"], payload["xaxis.range[1]"]]
                    start, end = (str(value)[:10] for value in values)
                    import pandas as pd
                    if "xaxis.range[0]" in payload and pd.Timestamp(end) - pd.Timestamp(start) == pd.Timedelta(days=1):
                        start = end
                expect(page.locator(f"#{label}")).to_contain_text(f"{start} to {end}")
                return start, end

            # Widen the viewport so the slider's two handles are easy to drag.
            commits.clear()
            buttons.filter(has_text="1M").click()
            wait_map_for_payload(wait_for_commit())
            page.wait_for_timeout(1300)
            commits.clear()
            before = page.locator(f"#{label}").inner_text()
            handle = page.locator(f"#{graph} .rangeslider-grabber-min").bounding_box()
            assert handle
            x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x - 10, y, steps=5)
            page.wait_for_timeout(250)
            assert not commits
            page.mouse.move(x - 25, y, steps=5)
            last_move = time.monotonic()
            page.wait_for_timeout(250)
            assert not commits, f"{name}: intermediate drag reached Python"
            assert page.locator(f"#{label}").inner_text() == before
            page.mouse.up()
            payload = wait_for_commit()
            assert commits[0]["time"] - last_move >= 0.8  # Scheduling tolerance.
            wait_map_for_payload(payload)
            assert len({json.dumps(item["value"], sort_keys=True) for item in commits}) == 1
            print(f"PASS: {name} two mouse moves commit only the final range after settling; map synchronized", flush=True)

            for preset in ["1D", "1W", "1M", "1Y"]:
                commits.clear()
                buttons.filter(has_text=preset).click()
                wait_map_for_payload(wait_for_commit())
                print(f"PASS: {name} native {preset} and map synchronization", flush=True)
            # Reset from a narrower view, using Plotly's documented public API.
            commits.clear()
            buttons.filter(has_text="1M").click()
            wait_map_for_payload(wait_for_commit())
            commits.clear()
            page.locator(f"#{graph} .js-plotly-plot").evaluate("el => Plotly.relayout(el, {'xaxis.autorange': true})")
            start, end = wait_map_for_payload(wait_for_commit())
            print(f"PASS: {name} autorange restores the analysis year", flush=True)
            if name == "crime":
                start_input = page.locator("#crime-analysis-start-date-input")
                prior = start_input.input_value()
                start_input.fill("2000-01-01")
                start_input.press("Enter")
                expect(start_input).to_have_value(prior)
                start_input.fill(end)
                start_input.press("Enter")
                expect(page.locator(f"#{label}")).to_contain_text(f"{end} to {end}")
                print("PASS: crime manual date validation and synchronization", flush=True)
            page.remove_listener("request", observe)

        # Visual overlap check uses the real figure builder with synthetic points.
        from test_range_debounce import overlapping_crime_figure
        figure = overlapping_crime_figure()
        figure.update_layout(mapbox_style="white-bg", mapbox_center={"lat": 47.6, "lon": -122.33},
                             mapbox_zoom=14, showlegend=False, margin={"l": 0, "r": 0, "t": 0, "b": 0},
                             width=400, height=400, title=None)
        page.set_content(figure.to_html(include_plotlyjs=True, full_html=True))
        page.wait_for_timeout(2000)
        screenshot = page.locator(".plotly-graph-div").screenshot()
        output = ROOT / "tests/_tmp/range_debounce_person_overlap.png"
        output.write_bytes(screenshot)
        pixel = Image.open(io.BytesIO(screenshot)).convert("RGB").getpixel((200, 200))
        assert pixel[0] > 180 and pixel[1] < 140 and pixel[2] < 140, pixel
        print(f"PASS: overlapping point center is persons red ({pixel}); screenshot {output}", flush=True)
        browser.close()


if __name__ == "__main__":
    run()
