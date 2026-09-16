# encoding:utf-8
"""Resolve the project's own site address for the console's 「帮助与关于」 entry.

Why this module exists
----------------------
The account menu's 「帮助与关于」 used to reuse the brand-version row
(``#sidebar-version``) href, so both entries opened one hard-coded operator
address that had nothing to do with the instance the user is looking at
(change ``help-about-project-site-link``). The entry now opens the product's own
site (``webhelp/``), whose address the site itself declares in
``webhelp/includes/config.php`` as ``site_url``.

That declared value is the **single source** for this target. This module owns
the whole "is it configured?" decision so no caller re-implements it:

* the file or the key is missing, the value is empty, or the value is still the
  scaffold placeholder (``http://YOUR-SITE-DOMAIN``)  -> unconfigured;
* the value is not an absolute ``http``/``https`` address with a host -> invalid,
  treated as unconfigured;
* anything else -> normalized ``scheme://host[:port][/path]/`` with the query
  and fragment dropped (a base address carries neither).

Every unconfigured/invalid case answers :data:`DEFAULT_HELP_SITE_URL`
(``http://localhost:8080/``, where the site is served locally with
``php -S 127.0.0.1:8080 -t webhelp``), so the entry is never dead.
"""

from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import urlsplit

#: Target used when the site has not declared (or cannot declare) its address.
DEFAULT_HELP_SITE_URL = "http://localhost:8080/"

#: Repo-relative location of the site configuration that declares the address.
SITE_CONFIG_RELATIVE_PATH = ("webhelp", "includes", "config.php")

#: The key holding the address, as written in that PHP array literal.
_SITE_URL_KEY = "site_url"

#: Host markers that mean "the scaffold value was never replaced". Kept as a
#: marker list (not an exact-value compare) so a reworded placeholder still
#: reads as unconfigured as long as it keeps the obvious "your-..." shape.
_PLACEHOLDER_HOST_MARKERS = ("your-site-domain",)

#: ``'site_url' => 'value'`` / ``"site_url" => "value"``, on one line.
_SITE_URL_RE = re.compile(
    r"""['"]%s['"]\s*=>\s*(?P<quote>['"])(?P<value>[^\n]*?)(?P=quote)""" % _SITE_URL_KEY
)


def site_config_path() -> str:
    """Absolute path of the site config file (``channel/web`` -> repo root)."""
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(project_root, *SITE_CONFIG_RELATIVE_PATH)


def read_declared_site_url(path: Optional[str] = None) -> str:
    """The raw ``site_url`` literal, or ``''`` when the file/key is absent.

    Deliberately a literal read, not a PHP evaluation: the file is a static
    array of scalars, and evaluating PHP to read one value would be a far larger
    surface than this address is worth.
    """
    target = path or site_config_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError):
        return ""
    match = _SITE_URL_RE.search(content)
    return match.group("value").strip() if match else ""


def normalize_site_url(raw: Optional[str]) -> str:
    """Normalize a declared address; ``''`` means "not usable as a target"."""
    value = (raw or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        port = parts.port  # also raises on a malformed 'host:port'
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    host = parts.hostname or ""
    if not host:
        return ""
    if any(marker in host.lower() for marker in _PLACEHOLDER_HOST_MARKERS):
        return ""
    # Rebuild from the parsed parts so credentials, query and fragment cannot
    # survive into a link target; keep an IP-literal host bracketed.
    authority = "[%s]" % host if ":" in host else host
    if port:
        authority = "%s:%d" % (authority, port)
    path = parts.path or "/"
    if not path.endswith("/"):
        path += "/"
    return "%s://%s%s" % (parts.scheme, authority, path)


def resolve_help_site_url(path: Optional[str] = None) -> str:
    """Final, directly openable address for 「帮助与关于」.

    Never raises and never returns an empty string: an unreadable file, an
    absent key, a placeholder or an invalid value all answer
    :data:`DEFAULT_HELP_SITE_URL`.
    """
    try:
        return normalize_site_url(read_declared_site_url(path)) or DEFAULT_HELP_SITE_URL
    except Exception:  # pragma: no cover - defensive: a target must always exist
        return DEFAULT_HELP_SITE_URL
