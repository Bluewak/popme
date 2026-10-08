"""POPME 전용 Chrome을 띄우고 Playwright로 연결한다.

평소 쓰는 기본 프로필은 Chrome 136부터 원격 디버깅 연결이 막혀 있어서,
별도 프로필(DATA_DIR/chrome-profile)에 한 번 로그인해 두고 계속 쓴다.
헤드리스·자동화 플래그 없이 보통 Chrome으로 실행하고 CDP로 붙는다.
"""
import json
import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from popme.config import CHROME_PROFILE

log = logging.getLogger(__name__)

CANDIDATES = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]


def chrome_exe() -> str:
    for p in CANDIDATES:
        if p.exists():
            return str(p)
    raise RuntimeError("Chrome을 찾지 못했어요. Google Chrome을 설치해 주세요.")


def is_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
            json.load(r)
        return True
    except Exception:
        return False


def ensure_chrome(port: int, urls=()):
    if is_running(port):
        return
    args = [
        chrome_exe(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={CHROME_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        # 창이 가려지거나 최소화돼도 스크롤·로딩이 멈추지 않게
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        *(urls or ["about:blank"]),
    ]
    subprocess.Popen(args, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    for _ in range(40):
        if is_running(port):
            return
        time.sleep(0.5)
    raise RuntimeError("POPME 전용 Chrome에 연결하지 못했어요.")


def open_for_login(port: int):
    """처음 한 번: 전용 Chrome에서 X·Threads·TimeTree에 로그인하도록 탭을 연다."""
    urls = ["https://x.com/login", "https://timetreeapp.com/signin", "https://www.threads.com/login"]
    if is_running(port):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0]
            for u in urls:
                ctx.new_page().goto(u)
    else:
        ensure_chrome(port, urls)


def set_minimized(page, minimized: bool):
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")
        cdp.send("Browser.setWindowBounds", {
            "windowId": win["windowId"],
            "bounds": {"windowState": "minimized" if minimized else "normal"},
        })
    except Exception as e:  # 최소화는 편의 기능이라 실패해도 진행
        log.warning("창 상태 변경 실패: %s", e)
