# encoding:utf-8
"""Target network policy and the redirect-aware fetch (task group 4).

Subject under test: ``integrations/external/adapters/netpolicy.py``.

The spec is explicit that the target address is validated, not just the name
("验证初始地址、DNS 解析及每次重定向;固定认证 origin,不向跨源跳转转发认证头;
SSRF 校验与实际连接使用同一解析结果"), that TLS verification is never silently
downgraded, and that the default deployment denies what it was not told about.

No real DNS and no real socket is used: the resolver and the HTTP transport are
both stubbed, so the file is offline and deterministic.
"""

from __future__ import annotations

import ipaddress
import json
import socket

import pytest
import requests

from integrations.external.adapters.base import ExecutionContext, PolicyRefused
from integrations.external.adapters.netpolicy import (
    NetworkPolicy,
    _safe_detail,
    current_policy,
    policy_from_mapping,
    reset_policy_cache,
    safe_get,
)

PUBLIC = "93.184.216.34"
PUBLIC_2 = "93.184.216.35"
PRIVATE = "10.0.0.5"


def _resolver(*addresses):
    """A stand-in for ``socket.getaddrinfo`` returning fixed addresses."""

    def fake(host, port, *args, **kwargs):
        out = []
        for address in addresses:
            if ":" in address:
                out.append((socket.AF_INET6, socket.SOCK_STREAM,
                            socket.IPPROTO_TCP, "", (address, int(port), 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM,
                            socket.IPPROTO_TCP, "", (address, int(port))))
        return out

    return fake


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    """Every name in this file resolves to one public address by default."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC))


def _ctx(policy, **overrides) -> ExecutionContext:
    values = dict(
        kind="probe_test_kind", scope="tenant", tenant_id="t1",
        owner_user_id=None, connection_id="conn-1", config={},
        secret_resolver=lambda slot: "", limits={"policy": policy},
    )
    values.update(overrides)
    return ExecutionContext(**values)


class _Response:
    """The minimum of ``requests.Response`` that ``safe_get`` touches."""

    def __init__(self, status_code=200, headers=None, chunks=(b"",)):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._chunks = list(chunks)
        self.closed = False

    def iter_content(self, size):
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True


def _install_transport(monkeypatch, handler):
    """Replace ``requests.get``; ``handler(url)`` returns a response or raises."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append({
            "url": url,
            "headers": dict(kwargs.get("headers") or {}),
            "allow_redirects": kwargs.get("allow_redirects"),
            "timeout": kwargs.get("timeout"),
        })
        result = handler(url)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


# -- the default policy denies what it was not told about --------------------

def test_the_default_policy_refuses_a_public_host_it_was_not_told_about():
    with pytest.raises(PolicyRefused):
        NetworkPolicy().check("https://example.com/")


def test_the_default_policy_refuses_plain_http():
    policy = NetworkPolicy(allow_hosts=frozenset({"example.com"}))
    with pytest.raises(PolicyRefused) as caught:
        policy.check("http://example.com/")
    assert "http" in str(caught.value).lower()


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/data",
    "gopher://example.com/",
    "example.com",
])
def test_a_non_http_scheme_is_refused(url):
    # ``allow_private`` skips the *name* check, so the scheme refusal is what
    # is actually under test here.
    policy = NetworkPolicy(allow_private=True)
    with pytest.raises(PolicyRefused):
        policy.check(url)


# -- addresses that are refused even where private ranges are allowed --------

@pytest.mark.parametrize("address", [
    "169.254.169.254", "100.100.100.200", "fd00:ec2::254",
])
def test_metadata_addresses_are_refused_even_with_private_allowed(address):
    assert NetworkPolicy(allow_private=True).address_allowed(address) is False


@pytest.mark.parametrize("address", [
    "127.0.0.1", "::1",              # loopback
    "169.254.1.1", "fe80::1",        # link-local
    "224.0.0.1", "ff02::1",          # multicast
    "240.0.0.1",                     # reserved
    "0.0.0.0", "::",                 # unspecified
    "fc00::1", "fd00::1",            # IPv6 ULA
])
def test_special_use_addresses_are_refused_by_default(address):
    assert NetworkPolicy().address_allowed(address) is False


@pytest.mark.parametrize("address", [
    "127.0.0.1", "::1",              # loopback
    "169.254.1.1", "fe80::1",        # link-local
    "224.0.0.1", "ff02::1",          # multicast
    "240.0.0.1",                     # reserved
    "0.0.0.0", "::",                 # unspecified
])
def test_non_routable_addresses_are_refused_even_with_private_allowed(address):
    # Granting private ranges must not also grant loopback/link-local/etc.
    assert NetworkPolicy(allow_private=True).address_allowed(address) is False


def test_private_ranges_follow_the_deployment_flag():
    assert NetworkPolicy().address_allowed(PRIVATE) is False
    assert NetworkPolicy(allow_private=True).address_allowed(PRIVATE) is True


def test_an_explicit_network_grant_is_the_only_way_to_reach_a_metadata_address():
    granted = NetworkPolicy(
        allow_networks=frozenset({ipaddress.ip_network("169.254.169.254/32")}))
    assert granted.address_allowed("169.254.169.254") is True
    # Being private is not enough on its own: metadata stays refused.
    assert NetworkPolicy(allow_private=True).address_allowed(
        "169.254.169.254") is False


def test_policy_from_mapping_honours_an_explicit_metadata_grant():
    policy = policy_from_mapping({"allow_networks": ["169.254.169.254/32"]})
    assert policy.address_allowed("169.254.169.254") is True


# -- host patterns -----------------------------------------------------------

def test_a_wildcard_host_pattern_matches_subdomains_only():
    policy = NetworkPolicy(allow_hosts=frozenset({"*.corp.example.com"}))
    assert policy.host_allowed("sap.corp.example.com") is True
    assert policy.host_allowed("corp.example.com") is False
    assert policy.host_allowed("evilcorp.example.com") is False


def test_an_exact_host_pattern_matches_only_itself():
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}))
    assert policy.host_allowed("sap.example.com") is True
    assert policy.host_allowed("sap.example.com.evil.net") is False
    assert policy.host_allowed("other.example.com") is False


def test_host_matching_is_case_and_trailing_dot_insensitive():
    policy = NetworkPolicy(allow_hosts=frozenset({"*.Corp.Example.com"}))
    assert policy.host_allowed("SAP.CORP.EXAMPLE.COM.") is True
    assert policy.host_allowed("sap.corp.example.com") is True


# -- resolution is checked address by address --------------------------------

def test_a_permitted_name_resolving_to_a_denied_address_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(PRIVATE))
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}))
    with pytest.raises(PolicyRefused):
        policy.check("https://sap.example.com/")


def test_every_resolved_address_is_checked_not_just_the_first(monkeypatch):
    # One public and one private address: the public one must not be enough.
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC, PRIVATE))
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}))
    with pytest.raises(PolicyRefused):
        policy.check("https://sap.example.com/")


def test_a_name_resolving_only_to_public_addresses_is_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC, PUBLIC_2))
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}))
    targets, steps = policy.check("https://sap.example.com/")
    assert {t.address for t in targets} == {PUBLIC, PUBLIC_2}
    assert steps and steps[-1].allowed is True


# -- URL shape and ports -----------------------------------------------------

def test_a_url_with_embedded_credentials_is_refused():
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}))
    with pytest.raises(PolicyRefused):
        policy.check("https://user:pass@sap.example.com/")


def test_an_invalid_port_is_refused():
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}))
    with pytest.raises(PolicyRefused):
        policy.check("https://sap.example.com:not-a-port/")


def test_allow_ports_restricts_the_reachable_ports():
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}),
                           allow_ports=frozenset({8443}))
    with pytest.raises(PolicyRefused):
        policy.check("https://sap.example.com:443/")
    targets, _steps = policy.check("https://sap.example.com:8443/")
    assert targets[0].port == 8443


def test_an_empty_allow_ports_accepts_the_scheme_default_range():
    policy = NetworkPolicy(allow_hosts=frozenset({"sap.example.com"}))
    assert policy.port_allowed(443, "https") is True
    assert policy.port_allowed(8443, "https") is True
    assert policy.port_allowed(80, "http") is True
    assert policy.port_allowed(8080, "http") is True


# -- TLS: no silent downgrade ------------------------------------------------

def test_disabling_tls_verification_is_refused_unless_the_deployment_allows_it():
    with pytest.raises(PolicyRefused) as caught:
        NetworkPolicy().effective_verify(False)
    assert caught.value.code == "target_not_allowed"
    assert NetworkPolicy(allow_verify_ssl_off=True).effective_verify(False) is False
    assert NetworkPolicy().effective_verify(True) is True


# -- redirect origin and credentials ----------------------------------------

def test_a_cross_origin_redirect_is_allowed_but_loses_credentials():
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com", "b.example.com"}))
    steps = policy.check_redirect("https://a.example.com/x",
                                  "https://b.example.com/y", secret_bearing=True)
    assert steps[-1].allowed is True
    assert steps[-1].credentials_allowed is False


def test_a_same_origin_redirect_keeps_credentials():
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))
    steps = policy.check_redirect("https://a.example.com/x",
                                  "https://a.example.com/y", secret_bearing=True)
    assert steps[-1].allowed is True
    assert steps[-1].credentials_allowed is True


def test_a_redirect_to_a_denied_host_is_refused():
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))
    with pytest.raises(PolicyRefused):
        policy.check_redirect("https://a.example.com/x",
                              "https://evil.example.com/y")


# -- safe_get: the bounded, redirect-aware fetch -----------------------------

def test_a_normal_200_is_returned_whole(monkeypatch):
    policy = NetworkPolicy(allow_private=True)
    calls = _install_transport(
        monkeypatch, lambda url: _Response(200, chunks=[b"hello"]))
    outcome = safe_get(_ctx(policy), "https://svc.example.com/ok")
    assert outcome.ok is True
    assert outcome.status == 200
    assert outcome.body == b"hello"
    # Redirects are walked by hand so every hop can be re-checked.
    assert calls[0]["allow_redirects"] is False


def test_a_redirect_chain_rechecks_every_hop(monkeypatch):
    policy = NetworkPolicy(
        allow_hosts=frozenset({"a.example.com", "b.example.com"}))

    def handler(url):
        if url == "https://a.example.com/start":
            return _Response(302, headers={"Location": "https://b.example.com/next"})
        return _Response(200, chunks=[b"done"])

    calls = _install_transport(monkeypatch, handler)
    outcome = safe_get(_ctx(policy), "https://a.example.com/start")
    assert outcome.ok is True
    assert outcome.url == "https://b.example.com/next"
    assert [c["url"] for c in calls] == [
        "https://a.example.com/start", "https://b.example.com/next"]
    checked = {step.url for step in outcome.steps if step.allowed}
    assert "https://b.example.com/next" in checked


def test_a_redirect_to_a_denied_host_is_never_fetched(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))
    calls = _install_transport(
        monkeypatch,
        lambda url: _Response(302, headers={"Location": "https://evil.example.com/x"}))
    outcome = safe_get(_ctx(policy), "https://a.example.com/start")
    assert outcome.ok is False
    assert outcome.code == "target_not_allowed"
    # The denied hop was refused before any request carried the secret there.
    assert len(calls) == 1


def test_the_authorization_header_is_dropped_on_a_cross_origin_hop(monkeypatch):
    policy = NetworkPolicy(
        allow_hosts=frozenset({"a.example.com", "b.example.com"}))

    def handler(url):
        if url == "https://a.example.com/start":
            return _Response(302, headers={"Location": "https://b.example.com/next"})
        return _Response(200, chunks=[b"ok"])

    calls = _install_transport(monkeypatch, handler)
    outcome = safe_get(_ctx(policy), "https://a.example.com/start",
                       headers={"Authorization": "Bearer top-secret"},
                       secret_bearing=True)
    assert outcome.ok is True
    assert calls[0]["headers"].get("Authorization") == "Bearer top-secret"
    assert "Authorization" not in calls[1]["headers"]


def test_the_authorization_header_is_kept_on_a_same_origin_hop(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))

    def handler(url):
        if url == "https://a.example.com/start":
            return _Response(302, headers={"Location": "/next"})
        return _Response(200, chunks=[b"ok"])

    calls = _install_transport(monkeypatch, handler)
    outcome = safe_get(_ctx(policy), "https://a.example.com/start",
                       headers={"Authorization": "Bearer top-secret"},
                       secret_bearing=True)
    assert outcome.ok is True
    assert calls[1]["url"] == "https://a.example.com/next"
    assert calls[1]["headers"].get("Authorization") == "Bearer top-secret"


def test_too_many_redirects_is_refused(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}),
                           max_redirects=2)
    calls = _install_transport(
        monkeypatch,
        lambda url: _Response(302, headers={"Location": "https://a.example.com/loop"}))
    outcome = safe_get(_ctx(policy), "https://a.example.com/loop")
    assert outcome.ok is False
    assert outcome.code == "too_many_redirects"
    # Exactly the allowed hops were fetched; the one past the bound was not.
    assert len(calls) == policy.max_redirects + 1


def test_a_redirect_without_a_location_is_reported(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))
    _install_transport(monkeypatch, lambda url: _Response(302))
    outcome = safe_get(_ctx(policy), "https://a.example.com/start")
    assert outcome.ok is False
    assert outcome.code == "redirect_without_location"


def test_a_tls_error_maps_to_the_tls_stage(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))
    _install_transport(
        monkeypatch,
        lambda url: requests.exceptions.SSLError("certificate verify failed"))
    outcome = safe_get(_ctx(policy), "https://a.example.com/start")
    assert outcome.ok is False
    assert outcome.stage == "tls"
    assert outcome.code == "tls_failed"


def test_a_timeout_maps_to_the_timeout_stage(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))
    _install_transport(monkeypatch,
                       lambda url: requests.exceptions.Timeout("timed out"))
    outcome = safe_get(_ctx(policy), "https://a.example.com/start")
    assert outcome.ok is False
    assert outcome.stage == "timeout"
    assert outcome.code == "timeout"


def test_a_large_body_is_truncated_to_the_response_limit(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}),
                           max_response_bytes=8)
    _install_transport(
        monkeypatch, lambda url: _Response(200, chunks=[b"0123456789"]))
    outcome = safe_get(_ctx(policy), "https://a.example.com/big")
    assert outcome.ok is True
    assert outcome.body == b"01234567"


def test_a_remote_error_detail_never_leaks_the_request_url(monkeypatch):
    policy = NetworkPolicy(allow_hosts=frozenset({"a.example.com"}))
    url = "https://a.example.com/hook?token=super-secret-token"
    _install_transport(
        monkeypatch,
        lambda u: requests.exceptions.ConnectionError(
            "Max retries exceeded with url: %s" % url))
    outcome = safe_get(_ctx(policy), url)
    assert outcome.ok is False
    assert outcome.stage == "network"
    # A URL can carry a credential in its query string, so it must not reach
    # the redacted projection.
    assert "super-secret-token" not in outcome.detail
    assert url not in outcome.detail


def test_safe_detail_keeps_the_class_name_and_bounds_the_length():
    detail = _safe_detail(requests.exceptions.RequestException("x" * 500))
    assert detail.startswith("RequestException:")
    assert len(detail) <= 220
    assert _safe_detail(requests.exceptions.RequestException()) == "RequestException"


# -- policy_from_mapping -----------------------------------------------------

def test_policy_from_mapping_parses_a_real_block():
    policy = policy_from_mapping({
        "allow_hosts": ["sap.example.com", "*.corp.example.com"],
        "allow_networks": ["10.0.0.0/8", "192.168.0.0/16"],
        "allow_ports": [443, "8443"],
        "allow_private": True,
        "allow_insecure_http": "yes",
        "allow_verify_ssl_off": "off",
        "max_redirects": 5,
        "timeout": 12.5,
        "max_response_bytes": 4096,
        "exceptions": {"sap.example.com": "on-premise"},
    })
    assert policy.host_allowed("sap.example.com") is True
    assert policy.host_allowed("x.corp.example.com") is True
    assert policy.host_allowed("corp.example.com") is False
    assert ipaddress.ip_network("10.0.0.0/8") in policy.allow_networks
    assert policy.allow_ports == frozenset({443, 8443})
    assert policy.allow_private is True
    assert policy.allow_insecure_http is True
    assert policy.allow_verify_ssl_off is False
    assert policy.max_redirects == 5
    assert policy.default_timeout == pytest.approx(12.5)
    assert policy.max_response_bytes == 4096
    assert policy.exceptions == {"sap.example.com": "on-premise"}


def test_a_malformed_network_grant_is_ignored_not_treated_as_permission():
    policy = policy_from_mapping({"allow_networks": ["not-a-network", "10.0.0.0/8"]})
    assert ipaddress.ip_network("10.0.0.0/8") in policy.allow_networks
    # The bad entry did not widen anything: a private address outside the one
    # valid grant is still refused.
    assert policy.address_allowed("192.168.1.1") is False


def test_malformed_numbers_fall_back_to_the_defaults():
    policy = policy_from_mapping({
        "max_redirects": "many", "timeout": "soon", "max_response_bytes": "big"})
    assert policy.max_redirects == 3
    assert policy.default_timeout == pytest.approx(30.0)
    assert policy.max_response_bytes == 1024 * 1024


def test_a_malformed_port_entry_is_dropped():
    policy = policy_from_mapping({"allow_ports": ["abc", 443]})
    assert policy.allow_ports == frozenset({443})


def test_current_policy_follows_the_deployment_configuration(monkeypatch):
    from config import conf

    try:
        monkeypatch.setitem(conf(), "external_connections",
                            {"allow_hosts": ["a.example.com"]})
        reset_policy_cache()
        assert current_policy().host_allowed("a.example.com") is True
        monkeypatch.setitem(conf(), "external_connections",
                            {"allow_hosts": ["b.example.com"]})
        # A configuration change takes effect without a restart.
        assert current_policy().host_allowed("b.example.com") is True
        assert current_policy().host_allowed("a.example.com") is False
    finally:
        reset_policy_cache()


def test_policy_as_dict_is_a_serialisable_non_secret_projection():
    payload = NetworkPolicy(allow_verify_ssl_off=True).as_dict()
    assert json.dumps(payload)
    assert payload["allow_verify_ssl_off"] is True
