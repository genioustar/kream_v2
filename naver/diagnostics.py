"""네이버 접근 상태를 분류하고 개인정보 없는 실행 진단을 저장한다."""
import asyncio
import inspect
import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from common.logger import get_logger
from common.pacing import CrawlBlockedError

logger = get_logger("naver.diagnostics")

_REASONS = {
    "account_protection": "계정 보호조치 화면 감지 — 평소 사용하는 네이버 앱에서 계정 상태를 확인하세요",
    "login_required": "로그인 화면 감지 — 공개 상품을 비로그인 상태로 수집할 수 없어 중단합니다",
    "security_challenge": "보안 확인/접근 제한 화면 감지 — 자동 재시도 없이 중단합니다",
    "authenticated_session": "네이버 인증 쿠키 감지 — 개인 계정을 사용하지 않도록 중단합니다",
    "http_403": "네이버 HTTP 403 접근 제한 — 자동 재시도 없이 중단합니다",
    "http_429": "네이버 HTTP 429 요청 제한 — 자동 재시도 없이 중단합니다",
    "network_error": "페이지/상품 요청 실패 — 네트워크 상태와 진단 기록을 확인하세요",
    "product_list_timeout": "상품 목록 대기시간 초과 — 빈 목록·렌더링 지연·셀렉터 변경 여부를 확인하세요",
    "collection_error": "상품 수집 실패 — 진단 기록을 확인하세요",
}


def safe_url(url: str) -> str:
    """쿼리, fragment, 사용자 정보를 기록하지 않는다."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        host = parsed.hostname
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except (TypeError, ValueError):
        return ""


def _is_naver(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
        return host == "naver.com" or host.endswith(".naver.com")
    except (TypeError, ValueError):
        return False


def classify_access(
    url: str,
    protection_present: bool = False,
    login_present: bool = False,
    challenge_present: bool = False,
    authenticated: bool = False,
) -> str | None:
    """계정 보호 증거를 먼저 판정하고 일반 로그인 링크는 증거로 사용하지 않는다."""
    parsed = urlsplit(safe_url(url))
    if (
        parsed.hostname == "nid.naver.com"
        and parsed.path.rstrip("/") == "/user2/help/idSafetyRelease"
    ) or protection_present:
        return "account_protection"
    if authenticated:
        return "authenticated_session"
    if (
        parsed.hostname == "nid.naver.com"
        and parsed.path == "/nidlogin.login"
    ) or login_present:
        return "login_required"
    if challenge_present:
        return "security_challenge"
    return None


class NaverAccessError(CrawlBlockedError):
    def __init__(self, reason: str, report_path: Path):
        self.reason = reason
        self.report_path = report_path
        super().__init__(f"[{reason}] {_REASONS[reason]} (진단: {report_path})")


class NaverCollectionError(RuntimeError):
    def __init__(self, reason: str, report_path: Path):
        self.reason = reason
        self.report_path = report_path
        super().__init__(f"[{reason}] {_REASONS[reason]} (진단: {report_path})")


_PAUSE_REASONS = frozenset({"login_required", "security_challenge"})


class NaverDiagnostics:
    """한 스토어의 네이버 요청과 현재 화면을 관찰한다. 응답 본문은 읽지 않는다."""

    def __init__(self, page, site_name, requested_url, item_selector, output_dir, run_id=None):
        self.page = page
        self.item_selector = item_selector
        self.block_reason = None
        self.pause_reason = None
        self._blocked = asyncio.Event()
        self._last_check = 0.0
        self._listeners = []
        self._request_counts = Counter()
        self._http_counts = Counter()
        self._failed_counts = Counter()
        self._collection_failed_counts = Counter()
        self._collection_http_errors = Counter()
        started = datetime.now().astimezone()
        run_id = run_id or uuid4().hex
        site_slug = re.sub(r"[^A-Za-z0-9_-]", "_", site_name)[:80] or "store"
        self.report_path = Path(output_dir) / started.strftime("%Y%m%d") / "diagnostics" / f"naver_{run_id}_{site_slug}.json"
        self.data = {
            "schema_version": 1,
            "run_id": run_id,
            "site_name": site_name,
            "started_at": started.isoformat(timespec="seconds"),
            "stage": "start",
            "status": "running",
            "reason": None,
            "requested_url": safe_url(requested_url),
            "final_url": "",
            "scope": "*.naver.com document/xhr/fetch; no response bodies, headers or cookie values",
            "request_counts": {},
            "http_counts": {},
            "request_failure_counts": {},
            "collection_request_failure_counts": {},
            "collection_http_error_counts": {},
            "events": [],
            "events_dropped": 0,
            "card_count": None,
            "product_count": 0,
            "skip_counts": {},
            "failure_detail": None,
            "authenticated_session": False,
            "navigation_exception_class": None,
            "exception_class": None,
        }
        self._save()

    def _save(self):
        self.data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.data["request_counts"] = dict(self._request_counts)
        self.data["http_counts"] = dict(self._http_counts)
        self.data["request_failure_counts"] = dict(self._failed_counts)
        self.data["collection_request_failure_counts"] = dict(self._collection_failed_counts)
        self.data["collection_http_error_counts"] = dict(self._collection_http_errors)
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.report_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.report_path)
        except OSError as exc:
            error_class = type(exc).__name__
            if self.data.get("diagnostic_write_error") != error_class:
                logger.warning("진단 파일 저장 실패 (%s): %s", error_class, self.report_path)
            self.data["diagnostic_write_error"] = error_class

    def _event(self, kind, url="", **fields):
        event = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stage": self.data["stage"],
            "kind": kind,
            "url": safe_url(url),
            **fields,
        }
        if len(self.data["events"]) < 50:
            self.data["events"].append(event)
        else:
            self.data["events_dropped"] += 1

    def _relevant(self, request):
        return request.resource_type in ("document", "xhr", "fetch") and _is_naver(request.url)

    def _on_request(self, request):
        if self._relevant(request):
            self._request_counts[request.resource_type] += 1

    def _collection_request(self, request):
        if request.resource_type == "document":
            try:
                return request.frame == self.page.main_frame
            except Exception:
                return False
        parsed = urlsplit(safe_url(request.url))
        # 개인화·찜·광고 위젯은 비로그인 상태에서 403을 돌려줄 수 있다.
        # 알려진 공개 목록 경로만 중단 신호로 사용하고 나머지는 기록한다.
        return parsed.hostname in ("brand.naver.com", "smartstore.naver.com") and bool(
            re.match(r"^/(?:[in]/)?v\d+/(?:channels/[^/]+/)?(?:products|search|categories|category)(?:/|$)", parsed.path)
        )

    def _on_response(self, response):
        request = response.request
        if not self._relevant(request):
            return
        self._http_counts[str(response.status)] += 1
        collection_request = self._collection_request(request)
        if collection_request and response.status >= 400:
            self._collection_http_errors[str(response.status)] += 1
        reason = classify_access(response.url) if request.resource_type == "document" and collection_request else None
        if reason or response.status >= 400 or request.resource_type == "document":
            self._event("response", response.url, resource_type=request.resource_type, status=response.status, collection_request=collection_request)
        if reason:
            self._note_access(reason)
        elif collection_request and response.status in (403, 429):
            self._set_block(f"http_{response.status}")

    def _on_requestfailed(self, request):
        if self._relevant(request):
            self._failed_counts[request.resource_type] += 1
            collection_request = self._collection_request(request)
            if collection_request:
                self._collection_failed_counts[request.resource_type] += 1
            self._event("request_failed", request.url, resource_type=request.resource_type, collection_request=collection_request)

    def _set_block(self, reason):
        if self.block_reason is None or reason == "account_protection":
            self.block_reason = reason
        self.data["status"] = "blocked"
        self.data["reason"] = self.block_reason
        self._blocked.set()
        self._save()

    def _note_access(self, reason: str | None) -> None:
        """로그인·보안 확인은 사람이 끝낼 수 있으므로 중단하지 않는다."""
        if reason in _PAUSE_REASONS:
            self.pause_reason = reason
            self.data["pause_reason"] = reason
            return
        if reason == "authenticated_session":
            self.data["authenticated_session"] = True
            self.pause_reason = None
            self.data["pause_reason"] = None
            return
        self.pause_reason = None
        self.data["pause_reason"] = None
        if reason:
            self._set_block(reason)

    async def wait_until_resumed(self) -> None:
        """로그인·보안 확인이 끝날 때까지 프로세스를 유지한다."""
        site_name = self.data["site_name"]
        logger.warning("[%s] 로그인/보안 확인 화면 — 종료하지 않고 입력을 기다립니다", site_name)
        while self.pause_reason:
            prompt = (
                f"\n[{site_name}] 열린 Chrome에서 로그인/보안 확인을 완료한 뒤 "
                "Enter를 누르세요. 이 사이트를 중단하려면 q 입력: "
            )
            try:
                answer = await asyncio.to_thread(input, prompt)
            except EOFError as exc:
                raise RuntimeError(f"[{site_name}] 사용자 입력을 받을 수 없음") from exc
            if answer.strip().casefold() == "q":
                raise RuntimeError(f"[{site_name}] 사용자가 크롤링을 중단했습니다.")
            self.pause_reason = None
            await self.check(self.data["stage"])

    def attach(self):
        for event, callback in (
            ("request", self._on_request),
            ("response", self._on_response),
            ("requestfailed", self._on_requestfailed),
        ):
            self.page.on(event, callback)
            self._listeners.append((event, callback))

    async def check(self, stage, force=True):
        stage_changed = self.data["stage"] != stage
        self.data["stage"] = stage
        self.data["final_url"] = safe_url(self.page.url)
        self._note_access(classify_access(self.page.url))
        sampled = bool(self.block_reason) or force or time.monotonic() - self._last_check >= 0.5
        if sampled:
            self._last_check = time.monotonic()
            cookies = await self.page.context.cookies([
                "https://nid.naver.com/", "https://brand.naver.com/", "https://smartstore.naver.com/",
            ])
            authenticated = any(cookie["name"] in ("NID_AUT", "NID_SES") for cookie in cookies)
            self.data["authenticated_session"] = authenticated
            try:
                markers = await asyncio.wait_for(self.page.evaluate("""() => {
                    const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                        && getComputedStyle(el).visibility !== 'hidden';
                    const text = (document.body?.innerText || '').slice(0, 30000).replace(/\\s+/g, ' ');
                    const protection = /회원님의 아이디를 보호하고 있습니다|회원님의 아이디가 보호조치|아이디가 보호조치 되었습니다|보호조치된 아이디입니다/.test(text);
                    const password = Array.from(document.querySelectorAll('input[type="password"]')).some(visible);
                    const username = Array.from(document.querySelectorAll('input[name="id"], input[name="loginId"], input[autocomplete="username"]')).some(visible);
                    const challenge = /비정상적인 접근이 감지|접근이 제한되었습니다|자동입력 방지문자를 입력|자동입력 방지 문자를 입력/.test(text)
                        || Array.from(document.querySelectorAll('input[name="captcha"], input#captcha, input[name="captchaKey"]')).some(visible);
                    return {protection_present: protection, login_present: password && username, challenge_present: challenge};
                }"""), timeout=2)
                reason = classify_access(self.page.url, authenticated=authenticated, **markers)
                if markers.get("login_present") or reason == "login_required":
                    reason = "account_protection" if reason == "account_protection" else "login_required"
                if reason:
                    self._event("page_signal", self.page.url, reason=reason)
                self._note_access(reason)
                if not self.block_reason:
                    self.data["card_count"] = await self.page.locator(self.item_selector).count()
            except Exception as exc:
                # 이동 중 execution context 교체는 다음 짧은 점검에서 다시 확인한다.
                self.data["last_dom_check_exception_class"] = type(exc).__name__
        if sampled or stage_changed or self.block_reason:
            self._save()
        if self.block_reason:
            raise NaverAccessError(self.block_reason, self.report_path)

    async def run(self, awaitable, stage):
        """작업 도중 제한 응답/화면을 감지하면 작업을 취소하고 진단 예외를 전달한다."""
        try:
            await self.check(stage, force=False)
        except BaseException:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise
        operation = asyncio.ensure_future(awaitable)
        blocked = asyncio.create_task(self._blocked.wait())
        try:
            while not operation.done():
                await asyncio.wait((operation, blocked), timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                await self.check(stage, force=False)
            await self.check(stage, force=False)
            return await operation
        finally:
            for task in (operation, blocked):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, blocked, return_exceptions=True)

    def finish(self, status, reason=None):
        self.data["status"] = status
        self.data["reason"] = reason
        self.data["final_url"] = safe_url(self.page.url) if self.page else ""
        self.data["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self._save()

    def close(self):
        for event, callback in self._listeners:
            try:
                self.page.remove_listener(event, callback)
            except Exception as exc:
                logger.warning("진단 리스너 해제 실패 (%s)", type(exc).__name__)
        self._listeners.clear()
