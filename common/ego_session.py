"""ego lite Task Space 하나와 그 안의 작업 페이지.

Python 이 로컬 포트를 열고 ego-browser 가 그 포트로 접속한다.
"""

import asyncio
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

SPACE_NAME = "resell-sniper"
_HOST_PATH = Path(__file__).resolve().parent.parent / "ego" / "space_host.mjs"
_BRIDGE_DIR = Path(__file__).resolve().parent.parent / "output" / "session" / "ego-space"


def pop_utf8_lines(pending: bytes, chunk: bytes) -> tuple[bytes, list[str]]:
    """개행까지 모인 바이트만 디코드한다. 글자 중간의 TCP 조각은 남긴다."""
    data = pending + chunk
    newline = data.rfind(b"\n")
    if newline < 0:
        return data, []
    text = data[:newline].decode("utf-8")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return data[newline + 1 :], lines


class Headers:
    def __init__(self, raw):
        self._raw = {str(key).lower(): value for key, value in (raw or {}).items()}

    def get(self, key, default=None):
        return self._raw.get(str(key).lower(), default)


class Request:
    def __init__(self, url, resource_type, document, main_frame):
        self.url = url
        self.resource_type = resource_type
        self.frame = main_frame if document else object()
        self._document = document

    def is_navigation_request(self):
        return self._document


class Response:
    def __init__(self, url, status, headers, request, body):
        self.url = url
        self.status = status
        self.headers = Headers(headers)
        self.request = request
        self._body = body

    async def text(self):
        if self._body is None:
            raise RuntimeError("응답 본문이 없습니다.")
        return self._body

    async def json(self):
        return json.loads(await self.text())


def role_selector(role, name):
    escaped = str(name).replace("\\", "\\\\").replace("'", "\\'")
    return f"loc=role:{role}[name='{escaped}']"


_HAS_TEXT = re.compile(
    r"^(?:(?P<tag>[a-zA-Z][\w-]*)?:)?has-text\((?P<q>['\"])(?P<text>.*?)(?P=q)\)$"
)


def has_text_parts(selector: str | None) -> tuple[str, str] | None:
    if not selector:
        return None
    match = _HAS_TEXT.fullmatch(selector.strip())
    if not match:
        return None
    return match.group("tag") or "*", match.group("text")


def selector_name(selector: str | None) -> str | None:
    if not selector:
        return None
    for marker in ("name='", 'name="'):
        start = selector.find(marker)
        if start < 0:
            continue
        start += len(marker)
        end = selector.find(marker[-1], start)
        if end > start:
            return selector[start:end]
    return None


class Locator:
    def __init__(self, page, selector=None, regex_role=None, regex_text=None, index=None, root=None):
        self.page = page
        self.selector = selector
        self.regex_role = regex_role
        self.regex_text = regex_text
        self.index = index
        self.root = root

    def _spec(self):
        node = {"selector": self.selector, "index": self.index}
        if self.root is None:
            return node
        parent = self.root._spec()
        cursor = parent
        while cursor.get("child"):
            cursor = cursor["child"]
        cursor["child"] = node
        return parent

    def nth(self, index: int):
        base = (self.selector or "").split(" >> nth=")[0]
        return Locator(self.page, base, index=index, root=self.root)

    def locator(self, selector: str):
        return Locator(self.page, selector, root=self)

    @property
    def first(self):
        if self.regex_role or self.regex_text or (self.selector or "").startswith(("loc=", "text=")):
            if self.selector and ">> nth=" not in self.selector:
                return Locator(self.page, f"{self.selector} >> nth=0", root=self.root)
            return self
        if self.index is None:
            return self.nth(0)
        return self

    async def count(self):
        if self.regex_role:
            _role, pattern = self.regex_role
            return await self.page.evaluate(
                "(pattern) => [...document.querySelectorAll('button,a,input')]"
                ".filter((el) => new RegExp(pattern).test((el.innerText || el.value || '').trim())).length",
                pattern,
            )
        if self.regex_text:
            return await self.page.evaluate(
                "(pattern) => new RegExp(pattern).test(document.body ? document.body.innerText : '') ? 1 : 0",
                self.regex_text,
            )
        parts = has_text_parts(self.selector)
        if parts:
            tag, text = parts
            return await self.page.evaluate(
                "(arg) => [...document.querySelectorAll(arg.tag)]"
                ".filter((el) => (el.innerText || '').includes(arg.text)).length",
                {"tag": tag, "text": text},
            )
        if self.selector and self.selector.startswith("text="):
            text = self.selector[len("text="):].strip().strip('"')
            return await self.page.evaluate(
                "(text) => (document.body && document.body.innerText.includes(text)) ? 1 : 0",
                text,
            )
        css = (self.selector or "").split(" >> nth=")[0]
        return await self.page.evaluate(
            "(sel) => document.querySelectorAll(sel).length",
            css,
        )

    async def wait_for(self, state="visible", timeout=30_000):
        if (
            self.selector
            and not self.regex_role
            and not self.regex_text
            and not self.selector.startswith("text=")
            and not has_text_parts(self.selector)
        ):
            await self.page.client.call("wait_for_selector", {
                "selector": self.selector,
                "state": state,
                "timeout": timeout,
            }, timeout=timeout / 1000 + 5)
            return
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if await self.count():
                return
            await asyncio.sleep(0.1)
        raise TimeoutError("요소 대기 시간이 끝났습니다.")

    async def click(self, timeout=None, click_count=1):
        if click_count and click_count > 1:
            await self.evaluate(
                "(el) => { if (!el) return false; el.focus(); if (typeof el.select === 'function') el.select(); return true; }"
            )
            return
        if self.regex_role:
            _role, pattern = self.regex_role
            clicked = await self.page.evaluate(
                "(pattern) => { const found = [...document.querySelectorAll('button,a,input')]"
                ".find((el) => new RegExp(pattern).test((el.innerText || el.value || '').trim()));"
                " if (!found) return false; found.click(); return true; }",
                pattern,
            )
            if not clicked:
                raise TimeoutError("클릭할 요소가 없습니다.")
            return
        try:
            result = await self.page.client.call("click", {"selector": self.selector}, timeout=(timeout or 30_000) / 1000 + 5)
        except RuntimeError as exc:
            if "intercepts pointer events" not in str(exc):
                raise
            clicked = await self._dom_click()
            if not clicked:
                raise
            return
        self.page.url = result["url"]

    async def _dom_click(self) -> bool:
        if self.selector and not str(self.selector).startswith(("loc=", "text=")):
            return bool(await self.evaluate("(el) => { if (!el) return false; el.click(); return true; }"))
        name = selector_name(self.selector)
        if not name:
            return False
        return bool(await self.page.evaluate(
            """(name) => {
                const nodes = [...document.querySelectorAll("button,[role='button']")];
                const found = nodes.find((el) => (el.innerText || "").replace(/\\s+/g, " ").includes(name));
                if (!found) return false;
                found.click();
                return true;
            }""",
            name,
        ))

    async def all_text_contents(self):
        parts = has_text_parts(self.selector)
        if parts:
            tag, text = parts
            return await self.page.evaluate(
                "(arg) => [...document.querySelectorAll(arg.tag)]"
                ".map((el) => (el.innerText || '').trim())"
                ".filter((value) => value.includes(arg.text))",
                {"tag": tag, "text": text},
            )
        css = (self.selector or "").split(" >> nth=")[0]
        return await self.page.evaluate(
            "(sel) => [...document.querySelectorAll(sel)].map((el) => (el.innerText || '').trim())",
            css,
        )

    async def is_visible(self, timeout=0):
        deadline = time.monotonic() + max(timeout, 0) / 1000
        while True:
            visible = await self.evaluate(
                """(el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const shown = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                    return shown && style.visibility !== "hidden" && style.display !== "none";
                }"""
            )
            if visible or time.monotonic() >= deadline:
                return bool(visible)
            await asyncio.sleep(0.1)

    async def is_disabled(self):
        return bool(await self.evaluate(
            "(el) => !el || !!el.disabled || el.getAttribute('aria-disabled') === 'true'"
        ))

    async def fill(self, value, timeout=None):
        result = await self.page.client.call("fill", {
            "selector": self.selector,
            "value": value,
        }, timeout=(timeout or 30_000) / 1000 + 5)
        self.page.url = result["url"]

    async def evaluate(self, source, arg=None):
        params = {"spec": self._spec(), "source": source, "hasArg": arg is not None}
        if arg is not None:
            params["arg"] = arg
        result = await self.page.client.call("element_eval", params)
        return result["value"]

    async def get_attribute(self, name: str, timeout=None):
        return await self.evaluate("(el, attr) => el ? el.getAttribute(attr) : null", name)

    async def inner_text(self, timeout=None):
        if self.regex_role:
            _role, pattern = self.regex_role
            return await self.page.evaluate(
                "(pattern) => { const found = [...document.querySelectorAll('button,a,input')]"
                ".find((el) => new RegExp(pattern).test((el.innerText || el.value || '').trim()));"
                " return found ? (found.innerText || '').trim() : ''; }",
                pattern,
            )
        return await self.evaluate("(el) => el ? (el.innerText || '').trim() : ''")

    async def evaluate_all(self, source):
        result = await self.page.client.call("evaluate_all", {
            "selector": self.selector,
            "source": source,
        })
        return result["value"]


class _Keyboard:
    def __init__(self, page):
        self.page = page

    async def type(self, text: str):
        await self.page.client.call("keyboard_type", {"text": text})

    async def press(self, key: str):
        result = await self.page.client.call("keyboard_press", {"key": key})
        if result and result.get("url"):
            self.page.url = result["url"]


class _CookieJar:
    def __init__(self, page):
        self._page = page

    async def cookies(self, urls=None):
        return await self._page.cookies(urls)


class EgoPage:
    def __init__(self, client):
        self.client = client
        self.url = "about:blank"
        self.main_frame = object()
        self.context = _CookieJar(self)
        self.keyboard = _Keyboard(self)
        self._closed = False
        self._listeners = {"request": [], "response": [], "requestfailed": []}

    def is_closed(self):
        return self._closed

    async def close(self):
        self._closed = True

    def on(self, event, callback):
        self._listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        listeners = self._listeners.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    def locator(self, selector):
        return Locator(self, selector)

    def get_by_role(self, role, name=None, exact=None):
        if hasattr(name, "pattern"):
            return Locator(self, regex_role=(role, name.pattern))
        return Locator(self, role_selector(role, name or ""))

    def get_by_placeholder(self, text, exact=None):
        escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
        return Locator(self, f'input[placeholder="{escaped}"]')

    def get_by_text(self, text, exact=None):
        if hasattr(text, "pattern"):
            return Locator(self, regex_text=text.pattern)
        escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
        return Locator(self, f'text="{escaped}"')

    async def goto(self, url, wait_until=None, timeout=30_000):
        result = await self.client.call("goto", {"url": url, "timeout": timeout}, timeout=timeout / 1000 + 5)
        self.url = result["url"]
        document = result.get("response")
        if not document:
            return None
        request = Request(document.get("url", self.url), "document", True, self.main_frame)
        return Response(request.url, document.get("status", 0), document.get("headers") or {}, request, document.get("body"))

    async def title(self):
        result = await self.client.call("title")
        return result["title"]

    async def content(self):
        result = await self.client.call("content")
        return result["html"]

    async def evaluate(self, source, arg=None):
        params = {"source": source, "hasArg": arg is not None}
        if arg is not None:
            params["arg"] = arg
        result = await self.client.call("evaluate", params)
        return result["value"]

    async def wait_for_selector(self, selector, state="visible", timeout=30_000):
        if has_text_parts(selector):
            await Locator(self, selector).wait_for(state=state, timeout=timeout)
            return
        await self.client.call("wait_for_selector", {
            "selector": selector,
            "state": state,
            "timeout": timeout,
        }, timeout=timeout / 1000 + 5)

    async def wait_for_load_state(self, state="load", timeout=None):
        await self.client.call("wait_for_load_state", {"state": state})

    async def wait_for_timeout(self, timeout):
        await asyncio.sleep(timeout / 1000)

    async def wait_for_url(self, predicate, timeout=30_000):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            result = await self.client.call("url")
            self.url = result["url"]
            if predicate(self.url):
                return
            await asyncio.sleep(0.2)
        raise TimeoutError("URL 대기 시간이 끝났습니다.")

    async def screenshot(self, path, full_page=False, timeout=None):
        await self.client.call("screenshot", {"path": str(path), "fullPage": full_page})

    def _emit(self, event):
        document = bool(event.get("document"))
        request = Request(event.get("url", ""), event.get("resourceType", "other"), document, self.main_frame)
        kind = event.get("kind")
        if kind == "response":
            response = Response(
                request.url,
                event.get("status", 0),
                event.get("headers") or {},
                request,
                event.get("body"),
            )
            callbacks = list(self._listeners.get("response", []))
            payload = response
        elif kind == "request":
            callbacks = list(self._listeners.get("request", []))
            payload = request
        elif kind == "requestfailed":
            callbacks = list(self._listeners.get("requestfailed", []))
            payload = request
        else:
            return
        for callback in callbacks:
            result = callback(payload)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)

    async def cookies(self, urls=None):
        result = await self.client.call("cookies")
        return result["cookies"]


class EgoClient:
    def __init__(self):
        self.page = None
        self._proc = None
        self._next = 1
        self._lock = asyncio.Lock()
        self._stop = False
        self._thread = None
        self._loop = None
        self._stderr = None
        self._conn = None
        self._server = None
        self._pending = {}
        self._ready = None

    async def start(self):
        binary = shutil.which("ego-browser")
        if not binary:
            raise RuntimeError("ego-browser 를 찾을 수 없습니다. ego lite 를 설치한 뒤 다시 실행하세요.")
        self._loop = asyncio.get_running_loop()
        self._ready = asyncio.Event()
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(20)
        self._server = server
        port = server.getsockname()[1]
        _BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        script = f"const BRIDGE_PORT = {port};\n" + _HOST_PATH.read_text(encoding="utf-8")
        script_path = _BRIDGE_DIR / "host.mjs"
        script_path.write_text(script, encoding="utf-8")
        stderr_path = _BRIDGE_DIR / "stderr.log"
        self._stderr = stderr_path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [binary, "nodejs"],
            stdin=script_path.open("r", encoding="utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        try:
            self._conn, _addr = await asyncio.wait_for(asyncio.to_thread(server.accept), 20)
        except (TimeoutError, OSError) as exc:
            raise RuntimeError("ego-browser 가 연결되지 않았습니다.") from exc
        self._conn.settimeout(1)
        self._stop = False
        self._thread = threading.Thread(target=self._read_socket, daemon=True)
        self._thread.start()
        await asyncio.wait_for(self._ready.wait(), 20)
        logger.info("ego lite Task Space 준비: %s", SPACE_NAME)

    def _read_socket(self):
        pending = b""
        while not self._stop:
            try:
                chunk = self._conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            pending, lines = pop_utf8_lines(pending, chunk)
            for line in lines:
                message = json.loads(line)
                if message.get("ready"):
                    self._loop.call_soon_threadsafe(self._ready.set)
                elif message.get("event"):
                    self._loop.call_soon_threadsafe(self._dispatch, message)
                else:
                    future = self._pending.get(message.get("id"))
                    if future is not None:
                        self._loop.call_soon_threadsafe(self._settle, future, message)

    def _settle(self, future, message):
        if not future.done():
            future.set_result(message)

    def _dispatch(self, event):
        if self.page is not None:
            self.page._emit(event)

    async def call(self, op, params=None, timeout=60):
        async with self._lock:
            cmd_id = self._next
            self._next += 1
            future = self._loop.create_future()
            self._pending[cmd_id] = future
            payload = json.dumps({"id": cmd_id, "op": op, "params": params or {}}) + "\n"
            await asyncio.to_thread(self._conn.sendall, payload.encode("utf-8"))
            try:
                message = await asyncio.wait_for(future, timeout)
            finally:
                self._pending.pop(cmd_id, None)
            if not message.get("ok"):
                text = message.get("error") or op
                if "timeout" in text.lower():
                    raise PlaywrightTimeoutError(text)
                raise RuntimeError(text)
            return message.get("result")

    async def finish(self):
        if self._conn is not None and self._proc is not None and self._proc.poll() is None:
            try:
                await self.call("finish", {}, timeout=15)
            except Exception as exc:
                logger.warning("Task Space 종료 실패 (%s)", type(exc).__name__)
        self._stop = True
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

