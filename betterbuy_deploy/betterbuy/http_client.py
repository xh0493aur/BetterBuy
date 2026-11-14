"""统一 HTTP 访问层。

职责：
- 统一 User-Agent、超时、重试策略；
- 全局限速（默认每个请求间隔 1.2 秒），避免对公开站点造成压力；
- robots.txt 检查（明确禁止则不访问）；
- 把各种失败翻译成 ``FetchError``，带上可展示的具体原因；
- 解析短链接与重定向（Steam 的 s.team/a/xxx 之类）。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter

from .netguard import UnsafeUrl, check_url

__all__ = ["FetchError", "HttpClient", "client"]

USER_AGENT = os.environ.get(
    "BETTERBUY_UA",
    "BetterBuy/0.2 (personal price comparison; contact: local user)",
)
TIMEOUT = float(os.environ.get("BETTERBUY_TIMEOUT", "20"))
MIN_INTERVAL = float(os.environ.get("BETTERBUY_MIN_INTERVAL", "1.2"))
MAX_REDIRECTS = 6


class FetchError(Exception):
    """带失败类型的采集错误，message 可直接展示给用户。"""

    def __init__(self, message: str, kind: str = "unknown", http_status: int | None = None):
        super().__init__(message)
        self.message = message
        self.kind = kind          # blocked / robots / http / network / timeout / notfound / unsupported / unsafe
        self.http_status = http_status

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return f"[{self.kind}] {self.message}"


@dataclass
class _RateLimiter:
    """进程内简单的等间隔限速器。"""

    interval: float = MIN_INTERVAL
    _last: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self) -> None:
        with self._lock:
            gap = time.monotonic() - self._last
            if gap < self.interval:
                time.sleep(self.interval - gap)
            self._last = time.monotonic()


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            }
        )
        adapter = HTTPAdapter(max_retries=1, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.limiter = _RateLimiter(interval=MIN_INTERVAL)
        self.robots_cache: dict[str, RobotFileParser | None] = {}
        self.log: list[str] = []

    # ---------- robots ----------
    def _robots(self, url: str) -> RobotFileParser | None:
        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if origin in self.robots_cache:
            return self.robots_cache[origin]
        parser: RobotFileParser | None = None
        try:
            self.limiter.wait()
            resp = self.session.get(urljoin(origin, "/robots.txt"), timeout=10)
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
            elif resp.status_code in (401, 403):
                # 拿不到 robots 时保守放行：只访问单个公开商品页，不做批量抓取
                parser = None
        except requests.RequestException:
            parser = None
        self.robots_cache[origin] = parser
        return parser

    def check_robots(self, url: str) -> None:
        parser = self._robots(url)
        if parser is None:
            return
        if not parser.can_fetch(USER_AGENT, url):
            raise FetchError(
                "该网站的 robots.txt 不允许访问这个地址，已停止采集（不会绕过限制）",
                kind="robots",
            )

    # ---------- 请求 ----------
    def get(self, url: str, *, params=None, as_json: bool = False, allow_redirects: bool = True,
            timeout: float | None = None, check_robot: bool = True) -> requests.Response:
        try:
            target = check_url(url)
        except UnsafeUrl as exc:
            raise FetchError(str(exc), kind="unsafe") from exc

        if check_robot:
            self.check_robots(target)

        resp = self._get_with_retry(target, params=params, timeout=timeout,
                                    allow_redirects=allow_redirects)

        self.log.append(f"{resp.status_code} {resp.url}")

        if resp.status_code == 404:
            raise FetchError("页面不存在（404），请确认链接是否正确", kind="notfound", http_status=404)
        if resp.status_code in (401, 403, 429):
            raise FetchError(
                f"站点返回 {resp.status_code}，属于访问限制（可能要求登录或触发了风控）。"
                "本工具不会绕过验证码、登录或访问限制。",
                kind="blocked", http_status=resp.status_code,
            )
        if resp.status_code >= 400:
            raise FetchError(f"站点返回错误状态 {resp.status_code}", kind="http", http_status=resp.status_code)

        if as_json:
            try:
                resp.json()
            except ValueError as exc:
                raise FetchError("接口返回的不是 JSON（可能被重定向到登录/验证页）", kind="blocked") from exc
        return resp

    def _get_with_retry(self, target: str, *, params=None, timeout=None,
                        allow_redirects: bool = True, retries: int = 2):
        """瞬时网络/代理错误重试；4xx 不重试（避免对站点施压）。"""
        last: Exception | None = None
        for attempt in range(retries + 1):
            self.limiter.wait()
            try:
                return self.session.get(
                    target, params=params, timeout=timeout or TIMEOUT,
                    allow_redirects=allow_redirects,
                )
            except requests.Timeout as exc:
                last = exc
                if attempt == retries:
                    raise FetchError(
                        f"访问超时（{TIMEOUT:.0f} 秒，已重试 {retries} 次）：{target}", kind="timeout"
                    ) from exc
            except requests.RequestException as exc:
                last = exc
                if attempt == retries:
                    raise FetchError(
                        f"网络访问失败（已重试 {retries} 次）：{type(exc).__name__} {str(exc)[:100]}",
                        kind="network",
                    ) from exc
            time.sleep(1.2 * (attempt + 1))
        raise FetchError(f"网络访问失败：{last}", kind="network")

    def get_json(self, url: str, *, params=None) -> dict:
        return self.get(url, params=params, as_json=True).json()

    def resolve(self, url: str) -> tuple[str, list[str]]:
        """跟随重定向，返回最终地址与跳转链；用于短链接。"""
        try:
            target = check_url(url)
        except UnsafeUrl as exc:
            raise FetchError(str(exc), kind="unsafe") from exc
        chain: list[str] = [target]
        current = target
        for _ in range(MAX_REDIRECTS):
            self.limiter.wait()
            try:
                resp = self.session.get(current, timeout=TIMEOUT, allow_redirects=False, stream=True)
            except requests.RequestException as exc:
                raise FetchError(f"短链接解析失败：{str(exc)[:120]}", kind="network") from exc
            try:
                resp.close()
            finally:
                pass
            if resp.is_redirect and resp.headers.get("Location"):
                nxt = urljoin(current, resp.headers["Location"])
                try:
                    check_url(nxt)
                except UnsafeUrl as exc:
                    raise FetchError(str(exc), kind="unsafe") from exc
                chain.append(nxt)
                current = nxt
                if any(current.rstrip("/").endswith(c.rstrip("/")) for c in chain[:-1]):
                    break
                continue
            break
        return current, chain


#: 进程级共享客户端（限速器也随之共享）
client = HttpClient()
