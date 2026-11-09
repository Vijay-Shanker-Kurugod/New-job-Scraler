"""Read JavaScript-rendered career pages with a real (headless) Chromium browser.

Used as a second pass for generic career pages whose plain HTML shows no job links:
many sites (SAP SuccessFactors, Softgarden widgets, dvinci, custom React pages) build the
job list in the browser. Playwright's sync API is not thread-safe, so this runs one page at
a time with one shared browser.
"""
import re

COOKIE_BUTTONS = re.compile(r"^(alle akzeptieren|alle cookies akzeptieren|akzeptieren|accept all|accept|"
                            r"allow all|zustimmen|einverstanden|ok)$", re.I)


class Browser:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._b = self._pw.chromium.launch()
        self._ctx = self._b.new_context(locale="de-DE", user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"))

    def render(self, url, wait_ms=2500):
        """Return [(url, html), ...] for the page and its iframes after scripts ran ([] on failure)."""
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001 – busy pages never go idle; use what loaded
                pass
            for b in page.get_by_role("button").all()[:30]:
                try:
                    if COOKIE_BUTTONS.match((b.inner_text(timeout=500) or "").strip()):
                        b.click(timeout=1500); page.wait_for_timeout(800); break
                except Exception:  # noqa: BLE001
                    continue
            page.wait_for_timeout(wait_ms)
            for _ in range(3):  # lazy lists load on scroll
                page.mouse.wheel(0, 4000); page.wait_for_timeout(700)
            out = [(page.url, page.content())]
            # job lists inside iframes (e.g. embedded applicant systems)
            for fr in page.frames[1:]:
                try:
                    out.append((fr.url, fr.content()))
                except Exception:  # noqa: BLE001
                    pass
            return out
        except Exception:  # noqa: BLE001
            return []
        finally:
            page.close()

    def close(self):
        self._b.close(); self._pw.stop()
