"""Records the realtime demo GIF in the root README: two portal windows side by
side, Bob (the member `make seed` creates) on the left and the owner on the
right, both on Ajax's Team's Tasks. Bob adds tasks, gives one a due date,
attaches an image to one and assigns it to the owner, and completes another;
the owner's window follows live and opens the file Bob attached.

It drives a headless Chrome over the DevTools protocol, one isolated browser
context per person. Each signs in the way the portal's `/login/dev` does,
through the local stack's dev sign-in rather than WorkOS, over the Python
client in `clients/python/`, and lands on the seeded team org, not the
personal org every person also has. The task
list is emptied first, then both windows are screencast and the frames are
composed on one timeline into a GIF. The attached image is drawn here, so
the recording needs no file of its own.

Each window is under half the GIF's width, the size a README shows it at, and
a task row stays on one line at that width: after every step the recorder
checks both windows and stops, writing nothing, when a row wraps or the page
scrolls sideways.

    make up
    uv run python scripts/record_demo.py docs/media/realtime-demo.gif

`make demo-gif` runs the second line. Other stacks: --api and --portal (the
portal's build must point at the same API). Chrome: CHROME, or the default
install path. --theme picks light (the default) or dark. --still writes a PNG
per window instead, to check the layout.
"""

import argparse
import asyncio
import base64
import datetime
import io
import itertools
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from PIL import Image, ImageDraw

from acme.client.client import ApiClient
from acme.client.types import OrgKind, TaskScope, TaskStatus

WIDTH, HEIGHT = 420, 840  # each window in the GIF: portrait, height twice the width
SCALE = 2  # render at twice the size, then downscale, for crisp text
FPS = 12
GAP = 12
DEBUG_PORT = 49222
BACKDROP = {"light": (220, 220, 216), "dark": (38, 39, 46)}  # around the two windows
ACCENTS = [(26, 143, 77), (201, 106, 5), (82, 80, 214), (197, 47, 42), (255, 255, 255)]


def chrome_path() -> str:
    candidates = [
        os.environ.get("CHROME"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    sys.exit("no Chrome found; set CHROME to its executable")


class Api:
    """The two things the recorder asks of the API, over the Python client."""

    def __init__(self, base: str) -> None:
        self.base = base

    def _client(self, token: str | None = None) -> ApiClient:
        return ApiClient(self.base, app="portal", app_version="portal@demo", token=token)

    async def session(self, email: str) -> dict[str, Any]:
        """What the portal keeps in session storage once someone has signed in
        and chosen the seeded team; every person also has a personal org."""
        async with self._client() as client:
            login = await client.dev_sign_in(email)
            org = next(m.org for m in login.memberships if m.org.kind is OrgKind.team)
            issued = await client.exchange_session(login.token, org.id)
        return {"state": {"token": issued.token, "orgSlug": org.slug}, "version": 0}

    async def clear_tasks(self, token: str) -> int:
        cleared = 0
        async with self._client(token) as client:
            for status in (TaskStatus.open, TaskStatus.done):
                while True:
                    page = await client.tasks(status, TaskScope.team, limit=200)
                    if not page.items:
                        break
                    for task in page.items:
                        await client.delete_task(task.id, task.version)
                        cleared += 1
        return cleared


class Cdp:
    """A minimal DevTools protocol client: commands by id, events to handlers."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.handlers: list[Callable[[dict[str, Any]], None]] = []

    async def pump(self) -> None:
        async for raw in self.ws:
            message = json.loads(raw)
            future = self.pending.pop(message.get("id"), None)
            if future is not None:
                if "error" in message:
                    future.set_exception(RuntimeError(message["error"]))
                else:
                    future.set_result(message.get("result", {}))
            elif "method" in message:
                for handler in self.handlers:
                    handler(message)

    async def send(
        self, method: str, params: dict[str, Any] | None = None, session: str = ""
    ) -> Any:
        command_id = next(self.ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[command_id] = future
        payload: dict[str, Any] = {"id": command_id, "method": method, "params": params or {}}
        if session:
            payload["sessionId"] = session
        await self.ws.send(json.dumps(payload))
        return await future


class Window:
    def __init__(self, cdp: Cdp, name: str, session: str, zoom: float) -> None:
        self.cdp, self.name, self.session, self.zoom = cdp, name, session, zoom
        self.frames: list[tuple[float, bytes]] = []

    async def js(self, expression: str) -> Any:
        result = await self.cdp.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            self.session,
        )
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str) -> None:
        """Polls the page (there is no event to await) for up to 20 seconds."""
        for _ in range(200):
            if await self.js(expression):
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"{self.name}: {expression}")

    async def add_task(self, title: str) -> None:
        """Types the title into the one box and presses Enter."""
        await self.js("document.querySelector('input[aria-label=\"New task\"]').focus()")
        for character in title:
            await self.cdp.send("Input.insertText", {"text": character}, self.session)
            await asyncio.sleep(0.07)
        await asyncio.sleep(0.35)
        enter = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}
        await self.cdp.send(
            "Input.dispatchKeyEvent", {"type": "keyDown", "text": "\r", **enter}, self.session
        )
        await self.cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", **enter}, self.session)

    async def set_value(self, expression: str, value: str) -> None:
        """Sets an input the way React hears it. A date picker is drawn
        outside the page and never reaches a screencast, so the date is set
        here rather than picked."""
        await self.js(
            f"""(() => {{
              const field = {expression};
              const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
              set.call(field, {json.dumps(value)});
              field.dispatchEvent(new Event("input", {{ bubbles: true }}));
              return true;
            }})()"""
        )

    def _row(self, title: str) -> str:
        """A JS expression for the open or done row whose title is `title`."""
        return f"""[...document.querySelectorAll("section ul > li")].find((li) =>
              li.querySelector(".acme-task-title")?.firstChild?.textContent
                === {json.dumps(title)})"""

    def _button(self, title: str, label: str) -> str:
        """A JS expression for the row's button that says `label`."""
        return (
            f"[...{self._row(title)}.querySelectorAll('button')]"
            f".find((b) => b.textContent === {json.dumps(label)})"
        )

    async def _centre(self, expression: str) -> dict[str, float]:
        box = await self.js(
            f"""(() => {{
              const r = ({expression}).getBoundingClientRect();
              return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
            }})()"""
        )
        if not box:
            raise RuntimeError(f"{self.name}: nothing at {expression}")
        return box

    async def click(self, expression: str) -> None:
        """Brings the target into view the way a person scrolls to it, then
        clicks its centre; an open form can run below the window."""
        if await self.js(
            f"""(() => {{
              const target = {expression};
              const r = target.getBoundingClientRect();
              if (r.top >= 0 && r.bottom <= window.innerHeight) return false;
              target.scrollIntoView({{ block: "nearest", behavior: "smooth" }});
              return true;
            }})()"""
        ):
            await asyncio.sleep(0.7)
        box = await self._centre(expression)
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            event = {"type": kind, "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1}
            await self.cdp.send("Input.dispatchMouseEvent", event, self.session)

    async def complete(self, title: str) -> None:
        await self.click(f"{self._row(title)}.querySelector('input[type=\"checkbox\"]')")

    async def open(self, title: str) -> None:
        """Opens the row's edit form, where its due date and its files are."""
        await self.click(self._button(title, "edit"))

    async def close(self, title: str) -> None:
        await self.click(self._button(title, "close"))

    async def assign(self, title: str, assignee: str) -> None:
        """Picks the assignee in the open form. A select's menu is drawn
        outside the page too, so the choice is set the way React hears it."""
        await self.js(
            f"""(() => {{
              const select = {self._row(title)}.querySelector("form select");
              const option = [...select.options]
                .find((o) => o.textContent === {json.dumps(assignee)});
              const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
              set.call(select, option.value);
              select.dispatchEvent(new Event("change", {{ bubbles: true }}));
              return true;
            }})()"""
        )

    async def attach(self, title: str, path: str) -> None:
        """Hands a file to the open form's picker, as choosing it in the
        system's dialog would, then waits for its preview to load."""
        picker = await self.cdp.send(
            "Runtime.evaluate",
            {"expression": f"{self._row(title)}.querySelector('input[type=\"file\"]')"},
            self.session,
        )
        # The DOM domain resolves a node only once it has handed out the document.
        await self.cdp.send("DOM.getDocument", {"depth": 0}, self.session)
        node = await self.cdp.send(
            "DOM.requestNode", {"objectId": picker["result"]["objectId"]}, self.session
        )
        await self.cdp.send(
            "DOM.setFileInputFiles", {"files": [path], "nodeId": node["nodeId"]}, self.session
        )
        await self.preview_shown(title)

    async def preview_shown(self, title: str) -> None:
        """Waits for the open form's image preview to finish loading."""
        await self.wait_for(f"!!{self._row(title)}.querySelector('img')?.complete")

    async def set_due(self, title: str, due: datetime.date) -> None:
        """Sets the due date in the row's open form."""
        field = f"{self._row(title)}.querySelector('form input[type=\"date\"]')"
        await self.set_value(field, due.isoformat())

    async def save(self, title: str) -> None:
        await self.click(f"{self._row(title)}.querySelector('form button[type=\"submit\"]')")

    async def check_layout(self) -> None:
        """Every task row on one line with its whole title, and nothing wider
        than the window. The portal ends a title that does not fit in an
        ellipsis; the demo picks titles that fit, so it never shows one."""
        problems = await self.js(
            """(() => {
              const found = [];
              const page = document.documentElement;
              if (page.scrollWidth > window.innerWidth)
                found.push(`the page scrolls sideways: ${page.scrollWidth} > ${window.innerWidth}`);
              for (const title of document.querySelectorAll(".acme-task-title")) {
                const line = title.parentElement;
                const heights = [...line.children].map((c) => c.getBoundingClientRect().height);
                if (line.getBoundingClientRect().height > Math.max(...heights) + 1)
                  found.push(`the row "${title.textContent}" wraps`);
                if (title.scrollWidth > title.clientWidth)
                  found.push(`the title "${title.textContent}" is cut short`);
              }
              return found;
            })()"""
        )
        if problems:
            raise SystemExit(f"{self.name}: " + "; ".join(problems))


async def open_window(
    cdp: Cdp,
    api: Api,
    portal: str,
    name: str,
    email: str,
    zoom: float,
    theme: str,
) -> Window:
    context = (await cdp.send("Target.createBrowserContext"))["browserContextId"]
    target = await cdp.send(
        "Target.createTarget", {"url": "about:blank", "browserContextId": context}
    )
    attached = await cdp.send(
        "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
    )
    window = Window(cdp, name, attached["sessionId"], zoom)
    for domain in ("Page", "Runtime"):
        await cdp.send(f"{domain}.enable", {}, window.session)
    # Browser zoom: a wider CSS viewport rendered into the same pixels.
    metrics = {
        "width": round(WIDTH / zoom),
        "height": round(HEIGHT / zoom),
        "deviceScaleFactor": SCALE * zoom,
        "mobile": False,
    }
    await cdp.send("Emulation.setDeviceMetricsOverride", metrics, window.session)
    # The portal follows the system's light or dark mode; the recording names
    # one, so the GIF looks the same on every machine that records it.
    scheme = {"features": [{"name": "prefers-color-scheme", "value": theme}]}
    await cdp.send("Emulation.setEmulatedMedia", scheme, window.session)
    await cdp.send("Page.navigate", {"url": f"{portal}/login/dev"}, window.session)
    await window.wait_for("document.readyState === 'complete'")
    stored = json.dumps(json.dumps(await api.session(email)))
    scope = json.dumps(json.dumps({"state": {"taskScope": "team"}, "version": 0}))
    # The bearer lives in the tab's session storage, and the portal drops a
    # session it finds in local storage on load; the scope is a preference and
    # stays in local storage.
    await window.js(
        f"sessionStorage.setItem('acme.portal.session', {stored});"
        f"localStorage.setItem('acme.portal.preferences', {scope}); true"
    )
    await cdp.send("Page.navigate", {"url": f"{portal}/"}, window.session)
    await window.wait_for(
        "!!document.querySelector('[aria-label=\"live updates: open\"]')"
        " && [...document.querySelectorAll('h2')].some((h) => h.textContent.startsWith('Open'))"
    )
    # Done starts folded; the story ends with a task landing there, so the
    # window unfolds it before the recording starts, the way a person would.
    await window.js(
        """(() => {
          const done = [...document.querySelectorAll(".acme-fold-toggle")]
            .find((b) => b.textContent === "Done");
          if (done && done.getAttribute("aria-expanded") === "false") done.click();
          return true;
        })()"""
    )
    await asyncio.sleep(1.0)
    return window


async def story(owner: Window, bob: Window, image: str) -> None:
    """Bob adds and completes tasks on the left, gives one a due date and one
    an image; the owner watches, then opens the image. Both windows are
    checked after every step."""

    async def step(action: Awaitable[None], pause: float) -> None:
        await action
        await asyncio.sleep(pause)
        for window in (bob, owner):
            await window.check_layout()

    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    await asyncio.sleep(1.2)
    await step(bob.add_task("Migrate the DB"), 1.2)
    await step(bob.add_task("Review PR #42"), 1.2)
    await step(bob.open("Review PR #42"), 0.8)
    await step(bob.set_due("Review PR #42", tomorrow), 0.9)
    await step(bob.save("Review PR #42"), 1.6)
    await step(bob.add_task("New logo"), 1.2)
    await step(bob.open("New logo"), 0.8)
    await step(bob.assign("New logo", "Local Owner"), 0.6)
    await step(bob.attach("New logo", image), 1.6)
    await step(bob.save("New logo"), 1.6)
    await step(owner.open("New logo"), 0.6)
    await step(owner.preview_shown("New logo"), 2.0)
    await step(owner.close("New logo"), 0.8)
    await step(bob.complete("Migrate the DB"), 2.2)


def draw_logo(path: str) -> None:
    """The image Bob attaches: a small logo draft, drawn here."""
    image = Image.new("RGB", (480, 240), (82, 80, 214))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((170, 50, 310, 190), radius=28, fill=(255, 255, 255))
    draw.line((205, 122, 230, 147, 278, 94), fill=(26, 143, 77), width=16, joint="curve")
    image.save(path)


def frame_at(frames: list[tuple[float, bytes]], moment: float) -> bytes:
    """The last frame painted at or before the moment."""
    chosen = frames[0][1]
    for timestamp, data in frames:
        if timestamp > moment:
            break
        chosen = data
    return chosen


def compose(left: Window, right: Window, start: float, end: float, out: str, theme: str) -> None:
    decoded: dict[int, Image.Image] = {}

    def image(data: bytes) -> Image.Image:
        if id(data) not in decoded:
            raw = Image.open(io.BytesIO(data)).convert("RGB")
            decoded[id(data)] = raw.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        return decoded[id(data)]

    canvas_w, canvas_h = WIDTH * 2 + GAP * 3, HEIGHT + GAP * 2
    frames: list[Image.Image] = []
    durations: list[int] = []
    step_ms = round(1000 / FPS)
    moment, last = start, None
    while moment <= end:
        on_left, on_right = frame_at(left.frames, moment), frame_at(right.frames, moment)
        if (id(on_left), id(on_right)) == last:
            durations[-1] += step_ms  # an unchanged moment lengthens the frame before it
        else:
            canvas = Image.new("RGB", (canvas_w, canvas_h), BACKDROP[theme])
            canvas.paste(image(on_left), (GAP, GAP))
            canvas.paste(image(on_right), (GAP * 2 + WIDTH, GAP))
            frames.append(canvas)
            durations.append(step_ms)
            last = (id(on_left), id(on_right))
        moment += step_ms / 1000

    # One palette for the whole GIF, from frames across the recording plus the
    # app's accent colours, so the live dot and checkboxes keep their colour.
    samples = frames[:: max(1, len(frames) // 12)]
    sheet = Image.new("RGB", (canvas_w, canvas_h * (len(samples) + 1)))
    for n, sample in enumerate(samples):
        sheet.paste(sample, (0, canvas_h * n))
    swatch = canvas_w // len(ACCENTS)
    for n, colour in enumerate(ACCENTS):
        top = canvas_h * len(samples)
        sheet.paste(colour, (n * swatch, top, (n + 1) * swatch, top + canvas_h))
    palette = sheet.quantize(colors=160, method=Image.Quantize.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    quantized[0].save(
        out, save_all=True, append_images=quantized[1:], duration=durations, loop=0, optimize=True
    )
    seconds = sum(durations) / 1000
    print(f"{out}: {len(frames)} frames, {seconds:.1f}s, {os.path.getsize(out) / 1e6:.2f} MB")


async def still(window: Window, path: str) -> None:
    shot = await window.cdp.send("Page.captureScreenshot", {"format": "png"}, window.session)
    image = Image.open(io.BytesIO(base64.b64decode(shot["data"])))
    image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(path)
    print(path)


async def record(args: argparse.Namespace) -> None:
    api = Api(args.api)
    if not args.still:  # a still shows whatever the list holds; a recording starts empty
        owner_session = await api.session(args.owner)
        cleared = await api.clear_tasks(owner_session["state"]["token"])
        print(f"cleared {cleared} tasks")

    profile = tempfile.mkdtemp(prefix="acme-demo-chrome-")
    chrome = await asyncio.create_subprocess_exec(
        chrome_path(),
        "--headless=new",
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--force-color-profile=srgb",
        "--disable-extensions",
        "about:blank",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        ws_url = await debugger_url()
        async with websockets.connect(ws_url, max_size=None) as ws:
            cdp = Cdp(ws)
            pump = asyncio.create_task(cdp.pump())
            windows = [
                await open_window(cdp, api, args.portal, name, email, args.zoom, args.theme)
                for name, email in (("bob", args.member), ("owner", args.owner))
            ]
            bob, owner = windows
            if args.still:
                for window in windows:
                    await window.check_layout()
                    await still(window, args.out.removesuffix(".gif") + f"-{window.name}.png")
            else:
                await screencast(cdp, bob, owner, args.out, args.theme)
            pump.cancel()
    finally:
        chrome.terminate()
        await chrome.wait()
        shutil.rmtree(profile, ignore_errors=True)


async def debugger_url() -> str:
    def fetch() -> str:
        url = f"http://127.0.0.1:{DEBUG_PORT}/json/version"
        with urllib.request.urlopen(url) as response:
            return json.load(response)["webSocketDebuggerUrl"]

    for _ in range(100):
        try:
            return await asyncio.to_thread(fetch)
        except OSError:
            await asyncio.sleep(0.1)
    raise TimeoutError("Chrome did not open its debugging port")


async def screencast(cdp: Cdp, bob: Window, owner: Window, out: str, theme: str) -> None:
    """Bob on the left, the owner on the right."""
    by_session = {bob.session: bob, owner.session: owner}
    acks: set[asyncio.Task[Any]] = set()

    def on_frame(message: dict[str, Any]) -> None:
        if message["method"] != "Page.screencastFrame":
            return
        params, session = message["params"], message["sessionId"]
        frame = (params["metadata"]["timestamp"], base64.b64decode(params["data"]))
        by_session[session].frames.append(frame)
        ack = {"sessionId": params["sessionId"]}
        task = asyncio.ensure_future(cdp.send("Page.screencastFrameAck", ack, session))
        acks.add(task)
        task.add_done_callback(acks.discard)

    cdp.handlers.append(on_frame)
    for window in (bob, owner):
        options = {"format": "jpeg", "quality": 92, "everyNthFrame": 1}
        await cdp.send("Page.startScreencast", options, window.session)
    files = tempfile.mkdtemp(prefix="acme-demo-files-")
    image = os.path.join(files, "logo.png")
    draw_logo(image)
    await asyncio.sleep(0.8)
    try:
        start = time.time()
        await story(owner, bob, image)
        end = time.time()
    finally:
        shutil.rmtree(files, ignore_errors=True)
    for window in (bob, owner):
        await cdp.send("Page.stopScreencast", {}, window.session)
    compose(bob, owner, start, end, out, theme)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("out", help="the GIF to write, e.g. docs/media/realtime-demo.gif")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--portal", default="http://localhost:55173")
    parser.add_argument("--owner", default="owner@example.test")
    parser.add_argument("--member", default="bob@example.test")
    parser.add_argument("--zoom", type=float, default=0.9, help="browser zoom, e.g. 0.9 for 90%%")
    parser.add_argument("--theme", choices=["light", "dark"], default="light")
    parser.add_argument("--still", action="store_true", help="write a PNG per window instead")
    asyncio.run(record(parser.parse_args()))


if __name__ == "__main__":
    main()
