"""统一的浏览器启动逻辑。

优先用 Playwright 自带的 Chromium（CI 里 `playwright install chromium`）；
本机没下载浏览器时，退回已安装的 Edge / Chrome，保证 `demo` 开箱能跑。
"""

from __future__ import annotations

from playwright.sync_api import Browser, Playwright

CHANNELS = ("msedge", "chrome")


def launch(playwright: Playwright, *, headless: bool = True, slow_mo: int = 0) -> Browser:
    errors: list[str] = []
    try:
        return playwright.chromium.launch(headless=headless, slow_mo=slow_mo)
    except Exception as exc:  # noqa: BLE001 - 缺浏览器时继续尝试系统浏览器
        errors.append(f"chromium: {str(exc).splitlines()[0]}")

    for channel in CHANNELS:
        try:
            return playwright.chromium.launch(headless=headless, slow_mo=slow_mo, channel=channel)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{channel}: {str(exc).splitlines()[0]}")

    raise RuntimeError(
        "找不到可用浏览器。请运行 `playwright install chromium`，或安装 Edge/Chrome。\n" + "\n".join(errors)
    )
