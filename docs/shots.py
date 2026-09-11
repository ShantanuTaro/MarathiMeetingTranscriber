"""Regenerate docs/*.png from the real pages. Run it after a visible UI change.

    python docs/shots.py [portal|session|guide ...]

The portal shots need a live session and a job mid-flight, so the state is staged in
this server's own memory and served by the real endpoints: nothing here fakes a
response, and a page that regressed will regress in the screenshot too. The recording
names, the balance and the flag counts are invented; no meeting content is published
in this repo.

Driven over the DevTools protocol rather than with `--screenshot`, because the two
things these captures need are exactly the two that flag cannot do: a full-page
image, and `prefers-color-scheme: dark`.
"""
import base64
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from websockets.sync.client import connect

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # run it as `python docs/shots.py`

import app                                    # noqa: E402  (after the path fix)

PORT, CDP = 8941, 9341
CHROME = os.getenv("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
WIDTH = 1440

# A session and two jobs, one working and one finished. Same fields the real worker
# writes, so the cards are drawn by the same code paths as a real run.
CLIENT = "docs-shot"
TOKEN = "docs-shot-session"


def stage():
    now = time.time()
    app.SESSIONS[TOKEN] = {"asr": "sarvam", "key": "not-a-real-key", "spent": 31.15,
                           "balance": 100.0, "jobs": 2, "rate": 45}
    app.JOBS.clear()
    app.JOBS["shot-done"] = {
        "status": "done", "progress": 1.0, "name": "gram-sabha-2026-08-14.m4a",
        "size": 74 * 1048576, "asr": "sarvam", "client": CLIENT,
        "created": now - 900, "began": now - 880, "elapsed": 402.0, "audio": 3600.0,
        "low_confidence": 0, "latin_script": 23, "expires": now + 1580,
    }
    app.JOBS["shot-live"] = {
        "status": "transcribing · part 2 of 2", "progress": 0.5,
        "name": "taluka-review-2026-09-09.mp3", "size": 269 * 1048576,
        "asr": "sarvam", "client": CLIENT, "created": now - 300,
        "began": now - 280, "audio": 8000.0,
    }


# What the browser would have in storage after a first visit. Set before any of the
# page's own script runs, so the page asks for this client's jobs and this session's
# wallet on its very first request rather than after a visible repaint.
def storage(session):
    # a different client id when unstaged: the sample jobs belong to CLIENT, and a
    # first visit has to be a page with no work on it
    client = CLIENT if session else CLIENT + "-fresh"
    return (f"try{{localStorage.setItem('client',{json.dumps(client)});"
            + (f"sessionStorage.setItem('asr-session',{json.dumps(json.dumps({'token': TOKEN, 'asr': 'sarvam'}))});"
               if session else "sessionStorage.removeItem('asr-session');")
            + "}catch(e){}")


SHOTS = {
    # name:          (path,                    staged session + jobs)
    "portal":  ("/", True),
    "session": ("/", False),
    "guide":   ("/help/sarvam-api-key", False),
}


def serve():
    server = uvicorn.Server(uvicorn.Config(app.app, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server


def chrome():
    profile = tempfile.mkdtemp(prefix="shots-")
    proc = subprocess.Popen(
        [CHROME, "--headless", f"--remote-debugging-port={CDP}",
         f"--user-data-dir={profile}", "--disable-gpu", "--hide-scrollbars",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):                       # the port is open before the tab is
        try:
            with socket.create_connection(("127.0.0.1", CDP), 0.2):
                return proc
        except OSError:
            time.sleep(0.1)
    raise SystemExit("Chrome never opened its debugging port")


class Tab:
    """One CDP session, with the four commands these captures need."""

    def __init__(self):
        url = f"http://127.0.0.1:{CDP}/json/new?about:blank"
        req = urllib.request.Request(url, method="PUT")   # PUT since Chrome 111
        self.id = json.load(urllib.request.urlopen(req))["id"]
        ws = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json"))
        target = next(t for t in ws if t["id"] == self.id)
        self.stack = contextlib.ExitStack()   # connect() wants to be a context manager
        self.ws = self.stack.enter_context(
            connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024))
        self.n = 0

    def __call__(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:                            # events arrive on the same socket
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def close(self):
        self.stack.close()
        urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/close/{self.id}").read()


def shoot(path, session, dark, out):
    tab = Tab()
    try:
        tab("Page.enable")
        tab("Emulation.setDeviceMetricsOverride", width=WIDTH, height=900,
            deviceScaleFactor=1, mobile=False)
        tab("Emulation.setEmulatedMedia", media="screen", features=[
            {"name": "prefers-color-scheme", "value": "dark" if dark else "light"}])
        tab("Page.addScriptToEvaluateOnNewDocument", source=storage(session))
        tab("Page.navigate", url=f"http://127.0.0.1:{PORT}{path}")
        # fonts, then the page's own first round of fetches and the bar's transition
        tab("Runtime.evaluate", expression="document.fonts.ready", awaitPromise=True)
        time.sleep(2.5)
        size = tab("Page.getLayoutMetrics")["cssContentSize"]
        shot = tab("Page.captureScreenshot", captureBeyondViewport=True, clip={
            "x": 0, "y": 0, "width": WIDTH, "height": size["height"], "scale": 1})
        out.write_bytes(base64.b64decode(shot["data"]))
        print(f"{out.name}  {WIDTH}x{round(size['height'])}")
    finally:
        tab.close()


def main(which):
    stage()
    server, browser = serve(), chrome()
    try:
        for name in which:
            path, session = SHOTS[name]
            for dark in (False, True):
                shoot(path, session, dark,
                      HERE / f"{name}-{'dark' if dark else 'light'}.png")
    finally:
        browser.terminate()
        server.should_exit = True


if __name__ == "__main__":
    main(sys.argv[1:] or list(SHOTS))
