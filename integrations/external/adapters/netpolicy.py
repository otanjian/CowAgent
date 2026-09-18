# encoding:utf-8
"""Target network policy: what an outbound connection attempt may reach.

The spec is specific about this and it is the part most easily got wrong
("目标地址策略允许管理员配置确有需要的企业内网域名/网段/端口；默认拒绝未许可
loopback、链路本地、云元数据和非法 scheme。验证初始地址、DNS 解析及每次重定向；
固定认证 origin，不向跨源跳转转发认证头；SSRF 校验与实际连接使用同一解析结果").

Design consequences that are visible in the code:

* The policy is **deployment configuration**, resolved once per attempt from
  ``conf()["external_connections"]``. An adapter receives the already-built
  :class:`NetworkPolicy` inside its context and cannot widen it.
* Validation is **address-based, not name-based**. The host is resolved and
  every resulting address must be permitted; a permitted *name* that resolves
  to a denied address is refused. This is what closes the DNS-rebinding gap.
* The check happens **again on every redirect**, with the new host. Each hop
  is resolved and checked independently, and the number of hops is bounded.
* :func:`NetworkPolicy.origin_allowed` exists separately from address
  permission because authentication headers must not follow a redirect to a
  different origin ("不向跨源跳转转发认证头"): a hop can be reachable yet
  still not allowed to receive credentials.

Deliberately absent: any "allow everything" escape hatch that a request body
could reach. The only way to widen the policy is deployment configuration.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from integrations.external.adapters.base import PolicyRefused, STAGE_NETWORK

#: Schemes an outbound connection may use. Anything else (``file:``,
#: ``gopher:``, ``ftp:``, a bare host) is refused before resolution.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Always refused, whatever the deployment allows, unless it explicitly
#: re-permits the range. Cloud metadata endpoints are credential theft by
#: design, so they are called out rather than left to a generic rule.
_METADATA_ADDRESSES = (
    "169.254.169.254",          # AWS / Azure / GCP / OpenStack IMDS
    "169.254.170.2",            # AWS ECS task metadata
    "100.100.100.200",          # Alibaba Cloud metadata
    "fd00:ec2::254",            # AWS IMDSv6
)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_REDIRECTS = 3
_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ResolvedTarget:
    """One address a hostname resolved to, kept with its family for reporting."""

    host: str
    address: str
    port: int
    family: str


@dataclass(frozen=True)
class RedirectStep:
    """One hop of a redirect chain that was checked."""

    url: str
    allowed: bool
    reason: str = ""
    credentials_allowed: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "allowed": self.allowed,
            "reason": self.reason,
            "credentials_allowed": self.credentials_allowed,
        }


@dataclass
class NetworkPolicy:
    """The resolved policy for one deployment.

    Built by :func:`policy_from_config`. Instances are cheap to copy and safe
    to share; :meth:`check` performs resolution, so it is not cached across
    calls on purpose — a cached resolution is exactly the stale-answer bug the
    "同一解析结果" requirement is about.
    """

    #: Hostnames or suffixes explicitly permitted. ``("*.corp.example.com",)``
    #: matches ``sap.corp.example.com`` but not ``corp.example.com`` itself.
    allow_hosts: FrozenSet[str] = frozenset()
    #: Networks explicitly permitted, in CIDR form.
    allow_networks: FrozenSet[ipaddress.IPv4Network | ipaddress.IPv6Network] = \
        field(default_factory=frozenset)
    #: Ports that may be reached. Empty means "any port the scheme implies".
    allow_ports: FrozenSet[int] = frozenset()
    #: When true, private/loopback/link-local ranges are permitted because the
    #: deployment declared them (an on-premise SAP or OA host). Metadata
    #: addresses are still refused unless they appear in ``allow_networks``.
    allow_private: bool = False
    #: Permits plain ``http://``. Off by default: an external system reached
    #: with credentials should be https unless the deployment says otherwise.
    allow_insecure_http: bool = False
    #: Whether TLS certificate verification may be turned off per connection.
    #: Off by default; a connection asking for ``verify_ssl=false`` then reports
    #: a policy refusal instead of silently connecting insecurely.
    allow_verify_ssl_off: bool = False
    max_redirects: int = _DEFAULT_MAX_REDIRECTS
    default_timeout: float = _DEFAULT_TIMEOUT
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES
    #: Deployment exceptions, recorded for audit: ``{host: "why"}``.
    exceptions: Mapping[str, str] = field(default_factory=dict)

    # -- address and URL checks ---------------------------------------------

    def host_allowed(self, host: str) -> bool:
        host = str(host or "").strip().lower().rstrip(".")
        if not host:
            return False
        for pattern in self.allow_hosts:
            pattern = pattern.lower().rstrip(".")
            if pattern.startswith("*."):
                suffix = pattern[2:]
                if host.endswith("." + suffix) and host != suffix:
                    return True
            elif host == pattern:
                return True
        return False

    def address_allowed(self, address: str) -> bool:
        """Whether a resolved address may be connected to."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        # An explicit network grant always wins, which is how a deployment
        # re-permits an address it also lists as sensitive.
        for network in self.allow_networks:
            if ip.version == network.version and ip in network:
                return True
        if str(ip) in _METADATA_ADDRESSES:
            return False
        if ip.is_loopback or ip.is_link_local or ip.is_multicast \
                or ip.is_reserved or ip.is_unspecified:
            return False
        if getattr(ip, "is_site_local", False):  # IPv6 ULA
            return False
        if ip.is_private:
            return bool(self.allow_private)
        return True

    def port_allowed(self, port: int, scheme: str) -> bool:
        if self.allow_ports:
            return int(port) in self.allow_ports
        if scheme == "https":
            return int(port) in (443, 8443) or 1 <= int(port) <= 65535
        return int(port) in (80, 8080) or 1 <= int(port) <= 65535

    def resolve(self, host: str, port: int) -> List[ResolvedTarget]:
        """Resolve ``host`` to addresses. Raises :class:`PolicyRefused`.

        A resolution failure is a *network* problem rather than a policy one,
        but it is raised as a refusal here so the caller cannot accidentally
        proceed to connect by name (which would resolve again, possibly
        differently).
        """
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise PolicyRefused("cannot resolve %s: %s" % (host, exc)) from exc
        out: List[ResolvedTarget] = []
        seen = set()
        for family, _type, _proto, _canon, sockaddr in infos:
            address = str(sockaddr[0])
            key = (address, port)
            if key in seen:
                continue
            seen.add(key)
            out.append(ResolvedTarget(
                host=host, address=address, port=int(port),
                family="ipv6" if family == socket.AF_INET6 else "ipv4"))
        if not out:
            raise PolicyRefused("cannot resolve %s" % host)
        return out

    def check_url_permission(self, url: str, *, secret_bearing: bool = False
                             ) -> Tuple[str, int, str]:
        """The *offline* half of :meth:`check`: may this URL be attempted at all.

        Everything :meth:`check` verifies without asking DNS anything: the
        scheme is one we speak, plain ``http`` is permitted (or the deployment
        said so), the URL carries no embedded credentials, the port is allowed,
        and — when the deployment listed names rather than ranges — the name
        itself is on the list.

        Split out because the two halves answer different questions and belong
        at different seams. This half is a **deployment policy** fact with no
        I/O, so an adapter can refuse a target before it builds a transport and
        report the refusal as a staged result. The resolution half stays in
        :meth:`check`, at the point of connection, because the requirement is
        that the SSRF check and the connection use the *same* resolution — a
        resolution cached or performed earlier is exactly the stale answer that
        property exists to prevent.

        Returns ``(host, port, scheme)`` for a caller that wants to reuse the
        parse.
        """
        parsed = urlparse(str(url or "").strip())
        scheme = (parsed.scheme or "").lower()
        if scheme not in ALLOWED_SCHEMES:
            raise PolicyRefused("scheme %r is not allowed" % (scheme or "-"))
        if scheme == "http" and not self.allow_insecure_http:
            raise PolicyRefused(
                "plain http is not allowed for external connections")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            raise PolicyRefused("target has no host")
        if parsed.username or parsed.password:
            raise PolicyRefused("target must not embed credentials")
        try:
            port = int(parsed.port or (443 if scheme == "https" else 80))
        except ValueError as exc:
            raise PolicyRefused("target has an invalid port") from exc
        if not self.host_allowed(host):
            # A permitted *address* under a non-permitted name is deliberately
            # not enough when the deployment listed names: the name is what the
            # operator vetted.
            if not self.allow_networks and not self.allow_private:
                raise PolicyRefused("host %r is not in the allowed list" % host)
        if not self.port_allowed(port, scheme):
            raise PolicyRefused("port %d is not allowed" % port)
        return host, port, scheme

    def check(self, url: str, *, secret_bearing: bool = False
              ) -> Tuple[List[ResolvedTarget], List[RedirectStep]]:
        """Validate one URL and return its resolved targets.

        ``secret_bearing`` marks a request that will carry a credential, so the
        caller gets the same refusal for an insecure scheme whether or not it
        remembered to ask.
        """
        steps: List[RedirectStep] = []
        try:
            host, port, _scheme = self.check_url_permission(
                url, secret_bearing=secret_bearing)
        except PolicyRefused as exc:
            steps.append(RedirectStep(url=url, allowed=False, reason=str(exc)))
            raise

        targets = self.resolve(host, port)
        denied = [t for t in targets if not self.address_allowed(t.address)]
        # Every address must be permitted: a name resolving to both a public
        # and a private address must not be reachable through the public one
        # while a second connection attempt silently uses the private one.
        if denied:
            reason = ("host %s resolves to an address that is not allowed (%s)"
                      % (host, denied[0].address))
            steps.append(RedirectStep(url=url, allowed=False, reason=reason))
            raise PolicyRefused(reason)
        steps.append(RedirectStep(url=url, allowed=True,
                                  credentials_allowed=True))
        return targets, steps

    def check_redirect(self, from_url: str, to_url: str, *,
                       secret_bearing: bool = False) -> List[RedirectStep]:
        """Validate a redirect hop, including whether auth may follow it.

        Returns the accumulated steps so the caller can record the chain. A
        reachable hop whose origin differs is marked
        ``credentials_allowed=False`` rather than refused: following a public
        redirect to another public host is legitimate, forwarding the
        ``Authorization`` header to it is not.
        """
        targets, steps = self.check(to_url, secret_bearing=secret_bearing)
        del targets
        same_origin = _origin_of(from_url) == _origin_of(to_url)
        if secret_bearing and not same_origin:
            steps[-1] = RedirectStep(url=to_url, allowed=True,
                                     reason="cross-origin redirect: "
                                            "credentials withheld",
                                     credentials_allowed=False)
        return steps

    # -- TLS -----------------------------------------------------------------

    def effective_verify(self, requested: bool) -> bool:
        """The TLS verification setting an attempt should actually use.

        A connection asking to disable verification when the deployment does
        not permit it gets a refusal, not a silent downgrade ("关闭 TLS 或
        证书校验必须符合部署策略并显式显示风险状态，不能静默降级"). The
        caller stores this on the outcome so the console can display the risk
        state the attempt actually ran under.
        """
        if requested:
            return True
        if not self.allow_verify_ssl_off:
            raise PolicyRefused(
                "disabling TLS certificate verification is not permitted "
                "by this deployment")
        return False

    def limits(self) -> Dict[str, Any]:
        return {
            "timeout": self.default_timeout,
            "max_redirects": self.max_redirects,
            "max_response_bytes": self.max_response_bytes,
        }

    def as_dict(self) -> Dict[str, Any]:
        """A non-secret description, for the console's policy note and audit."""
        return {
            "allow_hosts": sorted(self.allow_hosts),
            "allow_networks": sorted(str(n) for n in self.allow_networks),
            "allow_ports": sorted(self.allow_ports),
            "allow_private": self.allow_private,
            "allow_insecure_http": self.allow_insecure_http,
            "allow_verify_ssl_off": self.allow_verify_ssl_off,
            "max_redirects": self.max_redirects,
            "default_timeout": self.default_timeout,
            "max_response_bytes": self.max_response_bytes,
            "exceptions": dict(self.exceptions),
        }


def _origin_of(url: str) -> Tuple[str, str, int]:
    parsed = urlparse(str(url or "").strip())
    scheme = (parsed.scheme or "").lower()
    try:
        port = int(parsed.port or (443 if scheme == "https" else 80))
    except ValueError:
        port = 0
    return scheme, (parsed.hostname or "").lower(), port


def _to_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _compile_networks(values: Iterable[Any]) -> FrozenSet[Any]:
    out = set()
    for raw in values or ():
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            out.add(ipaddress.ip_network(text, strict=False))
        except ValueError:
            # A malformed grant is ignored rather than treated as permission.
            continue
    return frozenset(out)


def policy_from_mapping(raw: Optional[Mapping[str, Any]]) -> NetworkPolicy:
    """Build a policy from the deployment configuration block."""
    data = dict(raw or {})
    hosts = frozenset(
        str(h or "").strip().lower()
        for h in (data.get("allow_hosts") or ())
        if str(h or "").strip())
    ports = frozenset(
        _to_int(p, 0) for p in (data.get("allow_ports") or ())
        if _to_int(p, 0) > 0)
    exceptions = {
        str(k).strip().lower(): str(v or "")
        for k, v in dict(data.get("exceptions") or {}).items()
        if str(k or "").strip()
    }
    return NetworkPolicy(
        allow_hosts=hosts,
        allow_networks=_compile_networks(data.get("allow_networks") or ()),
        allow_ports=ports,
        allow_private=_to_bool(data.get("allow_private"), False),
        allow_insecure_http=_to_bool(data.get("allow_insecure_http"), False),
        allow_verify_ssl_off=_to_bool(data.get("allow_verify_ssl_off"), False),
        max_redirects=max(0, _to_int(data.get("max_redirects"),
                                    _DEFAULT_MAX_REDIRECTS)),
        default_timeout=max(1.0, _to_float(data.get("timeout"),
                                          _DEFAULT_TIMEOUT)),
        max_response_bytes=max(1024, _to_int(data.get("max_response_bytes"),
                                            _DEFAULT_MAX_RESPONSE_BYTES)),
        exceptions=exceptions,
    )


_CONFIG_LOCK = threading.Lock()
_CACHED: Optional[Tuple[int, NetworkPolicy]] = None


def _config_signature(raw: Mapping[str, Any]) -> int:
    try:
        return hash(repr(sorted((str(k), repr(v)) for k, v in raw.items())))
    except Exception:  # noqa: BLE001 - an unhashable config simply re-reads
        return 0


def current_policy() -> NetworkPolicy:
    """The deployment's policy, re-read when the configuration changes.

    A policy change must take effect without a restart, and it must take
    effect for attempts that have not yet started. Each attempt therefore
    builds its context with a freshly-read policy (:meth:`ExecutionContext`
    holds the instance it was given).
    """
    global _CACHED
    try:
        from config import conf
        raw = (conf() or {}).get("external_connections") or {}
        if not isinstance(raw, Mapping):
            raw = {}
    except Exception:  # noqa: BLE001 - no config means the tight default
        raw = {}
    signature = _config_signature(raw)
    with _CONFIG_LOCK:
        if _CACHED is not None and _CACHED[0] == signature:
            return _CACHED[1]
    policy = policy_from_mapping(raw)
    with _CONFIG_LOCK:
        _CACHED = (signature, policy)
    return policy


def reset_policy_cache() -> None:
    """Drop the cached policy. For tests and for a config reload."""
    global _CACHED
    with _CONFIG_LOCK:
        _CACHED = None


# -- a bounded, redirect-aware fetch ----------------------------------------

@dataclass
class FetchOutcome:
    """The result of a policy-checked HTTP GET."""

    ok: bool
    status: int = 0
    body: bytes = b""
    url: str = ""
    steps: List[RedirectStep] = field(default_factory=list)
    code: str = ""
    stage: str = STAGE_NETWORK
    detail: str = ""

    @property
    def text(self) -> str:
        try:
            return self.body.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""

    def json(self) -> Any:
        import json
        return json.loads(self.body.decode("utf-8", "replace"))


def safe_get(ctx, url: str, *, headers: Optional[Mapping[str, str]] = None,
             secret_bearing: bool = False, timeout: Optional[float] = None,
             accept: str = "application/json, text/plain, */*") -> FetchOutcome:
    """GET ``url`` under the deployment policy, following bounded redirects.

    Uses ``requests`` with ``allow_redirects=False`` and walks the chain by
    hand, because the whole point is to re-check each hop and to decide
    per-hop whether credentials may travel. A library-managed redirect would
    do neither.
    """
    import requests

    policy = ctx.limits.get("policy") if isinstance(ctx.limits, Mapping) else None
    if not isinstance(policy, NetworkPolicy):
        policy = current_policy()
    budget = timeout if timeout is not None else policy.default_timeout
    steps: List[RedirectStep] = []
    current = url
    # Credentials are pinned to the origin they were issued for. A later hop
    # that lands on a different origin keeps the request but loses the header.
    credential_origin = _origin_of(url) if secret_bearing else None
    hop_headers = dict(headers or {})
    for _hop in range(policy.max_redirects + 1):
        ctx.check_alive()
        try:
            policy.check(current, secret_bearing=secret_bearing)
        except PolicyRefused as exc:
            return FetchOutcome(ok=False, url=current, steps=steps,
                                code=exc.code, stage=STAGE_NETWORK,
                                detail=str(exc))
        try:
            response = requests.get(
                current, headers=hop_headers, timeout=ctx.io_timeout(budget),
                allow_redirects=False, stream=True)
        except requests.exceptions.Timeout:
            return FetchOutcome(ok=False, url=current, steps=steps,
                                code="timeout", stage="timeout",
                                detail="the target did not respond in time")
        except requests.exceptions.SSLError as exc:
            return FetchOutcome(ok=False, url=current, steps=steps,
                                code="tls_failed", stage="tls",
                                detail=_safe_detail(exc))
        except requests.exceptions.RequestException as exc:
            return FetchOutcome(ok=False, url=current, steps=steps,
                                code="network_unreachable", stage=STAGE_NETWORK,
                                detail=_safe_detail(exc))
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            response.close()
            if not location:
                return FetchOutcome(ok=False, status=response.status_code,
                                    url=current, steps=steps,
                                    code="redirect_without_location")
            from urllib.parse import urljoin
            target = urljoin(current, location)
            if len(steps) >= policy.max_redirects:
                return FetchOutcome(ok=False, status=response.status_code,
                                    url=current, steps=steps,
                                    code="too_many_redirects")
            try:
                hop_steps = policy.check_redirect(
                    current, target, secret_bearing=secret_bearing)
            except PolicyRefused as exc:
                return FetchOutcome(ok=False, status=response.status_code,
                                    url=current, steps=steps,
                                    code=exc.code, stage=STAGE_NETWORK,
                                    detail=str(exc))
            steps.extend(hop_steps)
            current = target
            if credential_origin is not None and _origin_of(target) != credential_origin:
                hop_headers = {k: v for k, v in hop_headers.items()
                               if k.lower() != "authorization"}
            continue
        limit = policy.max_response_bytes
        body = b""
        try:
            for chunk in response.iter_content(8192):
                ctx.check_alive()
                body += chunk
                if len(body) > limit:
                    body = body[:limit]
                    break
        finally:
            response.close()
        return FetchOutcome(ok=200 <= response.status_code < 300,
                            status=response.status_code, body=body,
                            url=current, steps=steps,
                            code="" if response.status_code < 400
                            else "http_status",
                            stage=STAGE_NETWORK,
                            detail="" if response.status_code < 400
                            else "target returned HTTP %d" % response.status_code)
    return FetchOutcome(ok=False, url=current, steps=steps,
                        code="too_many_redirects")


def _safe_detail(exc: Exception) -> str:
    """A short, non-secret description of a network failure.

    A raw ``requests`` exception embeds the request in its message — commonly
    the full URL, which commonly carries a credential in its query string
    ("Max retries exceeded with url: https://host/hook?token=..."). Forwarding
    that message would put a live secret into a stored outcome and the console.

    So the URL is *removed*, not truncated: length is not redaction, and a
    secret that survives 200 characters is leaked just as completely as one
    that does not. What remains is the exception class and the prose around it,
    which is what makes a failure diagnosable without carrying the target.
    """
    text = " ".join(str(exc).split())
    text = _URL_RE.sub("<url>", text)
    # A query string can appear without a scheme (a relative hop, a bare host).
    text = _QUERY_RE.sub("?<redacted>", text)
    if len(text) > 200:
        text = text[:200] + "…"
    return "%s: %s" % (type(exc).__name__, text) if text else type(exc).__name__


#: An absolute URL, which is what carries a host, a path and a query string.
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")

#: Anything from a ``?`` to the next whitespace: a query string, whatever
#: preceded it.
_QUERY_RE = re.compile(r"\?\S*")
