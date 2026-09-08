# encoding:utf-8
"""Small shared helpers for web.py route tests.

Keeps cookie extraction in one place: since Web login returns no token in the
JSON body (only the HttpOnly session Cookie), tests that need a session token to
send subsequent requests read it from the ``Set-Cookie`` response header.
"""

import re


def cookie_value(response, name):
    """Return the value of the cookie ``name`` from a response's Set-Cookie.

    ``response`` is a ``web.storage`` as returned by ``app.request(...)``; it
    exposes ``header_items`` (list of ``(name, value)``). Returns "" when absent.
    """
    items = getattr(response, "header_items", None) or []
    for header_name, value in items:
        if header_name != "Set-Cookie":
            continue
        for part in value.split("; "):
            if part.startswith(name + "="):
                return part[len(name) + 1:]
    return ""


def has_cookie(response, name):
    """True when the response sets a cookie named ``name``."""
    return bool(cookie_value(response, name))
