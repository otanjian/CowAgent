"""Resolve which channel instances to run, bridging legacy and multi-instance.

A legacy install configures channels through ``channel_type`` (a list of type
names) plus flat credentials in ``config.json``; every type maps to exactly one
running channel bound to the default Agent. This is the "shared by default"
world and must keep working untouched.

A multi-Agent install opts in by adding ``channel_instances`` to ``team.json``:
a list of records, each with its own ``instance_id``, ``channel_type``, the
``agent_id`` it routes to, and its own ``credentials``. Several records may
share a ``channel_type`` (e.g. two Feishu bots), which is exactly what a team of
independent AI employees needs.

``resolve_channel_instances`` returns a uniform list of :class:`ChannelInstance`
for both worlds, so the launcher never branch on which config style is in play. When ``channel_instances`` is absent it synthesizes one
instance per legacy ``channel_type`` with no credential override, i.e. the old
behavior byte-for-byte.
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from common import const
from common.log import logger

# Length of the random suffix in a generated instance id. 10 hex chars gives
# ~40 bits of entropy, plenty to avoid collisions across a user's own bots while
# staying short enough to read and type.
_INSTANCE_ID_RANDOM_LEN = 10


def new_instance_id(channel_type: str, taken: Iterable[str] = ()) -> str:
    """Return a stable, unique id for a freshly created channel instance.

    Shape is ``{channel_type}-{random}`` (e.g. ``feishu-a3f9c2e1b7``): the
    prefix keeps the type recognizable at a glance, the random suffix makes it
    unique and stable across restarts. Ids provided elsewhere (e.g. handed down
    by a remote controller) are honored as-is and never regenerated; this helper
    is only for the local "create a new instance" path.
    """
    ctype = _normalize_type((channel_type or "").strip()) or "channel"
    existing = set(taken)
    while True:
        candidate = f"{ctype}-{secrets.token_hex(_INSTANCE_ID_RANDOM_LEN // 2 + 1)[:_INSTANCE_ID_RANDOM_LEN]}"
        if candidate not in existing:
            return candidate


# Per channel type, the config keys that make up its credentials. Only these
# keys are copied into a per-instance override; everything else (ports, feature
# flags) stays global in conf(). Keep in sync with the channel classes' cfg()
# reads. Extend as more channel types gain multi-instance support.
CREDENTIAL_KEYS: Dict[str, tuple] = {
    const.FEISHU: (
        "feishu_app_id",
        "feishu_app_secret",
        "feishu_token",
        "feishu_bot_name",
    ),
    const.DINGTALK: (
        "dingtalk_client_id",
        "dingtalk_client_secret",
        "dingtalk_robot_code",
    ),
    const.WECOM_BOT: (
        "wecom_bot_id",
        "wecom_bot_secret",
        "wecom_bot_token",
        "wecom_bot_encoding_aes_key",
    ),
    const.WEIXIN: (
        "weixin_token",
        "weixin_base_url",
    ),
    const.QQ: (
        "qq_app_id",
        "qq_app_secret",
    ),
    const.TELEGRAM: (
        "telegram_token",
    ),
    const.SLACK: (
        "slack_bot_token",
        "slack_app_token",
    ),
    const.DISCORD: (
        "discord_token",
    ),
}

#: Subset of :data:`CREDENTIAL_KEYS` without which a channel instance cannot
#: start. Kept beside the full key list rather than replacing it: the full list
#: still decides which field names a write may carry, so an optional field
#: (a verification token, a display name) stays storable and is simply not
#: required.
#:
#: Every entry is the *startup guard the channel class itself enforces*, verified
#: by reading that class, not guessed from the field names:
#:   feishu      feishu_channel.py     "app_id ... or app_secret" guard
#:   wecom_bot   wecom_bot_channel.py  "wecom_bot_id and wecom_bot_secret" guard
#:                                     (websocket mode; the webhook transport is
#:                                      single-instance and refuses extra
#:                                      instances, so token/aes are not needed
#:                                      for a tenant-owned bot)
#:   qq          qq_channel.py         "qq_app_id and qq_app_secret" guard
#:   telegram    telegram_channel.py   "telegram_token is required" guard
#:   slack       slack_channel.py      "slack_bot_token and slack_app_token" guard
#:   discord     discord_channel.py    "discord_token is required" guard
#:
#: ``dingtalk`` and ``weixin`` are deliberately absent: their minimum set has not
#: been verified against the channel classes yet, and inventing one would reject
#: writes that work today. A type without an entry here keeps the previous rule
#: (at least one declared, non-empty field).
REQUIRED_CREDENTIAL_KEYS: Dict[str, tuple] = {
    const.FEISHU: (
        "feishu_app_id",
        "feishu_app_secret",
    ),
    const.WECOM_BOT: (
        "wecom_bot_id",
        "wecom_bot_secret",
    ),
    const.QQ: (
        "qq_app_id",
        "qq_app_secret",
    ),
    const.TELEGRAM: (
        "telegram_token",
    ),
    const.SLACK: (
        "slack_bot_token",
        "slack_app_token",
    ),
    const.DISCORD: (
        "discord_token",
    ),
}


def required_credential_keys(channel_type: str) -> tuple:
    """Keys that must be present and non-empty for *channel_type* to start.

    Empty for a type whose minimum set has not been verified yet (see
    :data:`REQUIRED_CREDENTIAL_KEYS`), which leaves that type's validation to the
    "at least one declared field" rule it had before.
    """
    return REQUIRED_CREDENTIAL_KEYS.get(_normalize_type(channel_type), ())

# Channel types that actually support running more than one instance today.
# Others may appear in channel_instances but will run as a single instance
# (their @singleton is not yet bypassed); we log and fall back gracefully.
MULTI_INSTANCE_READY = frozenset({
    const.FEISHU,
    const.DINGTALK,
    const.QQ,
    const.TELEGRAM,
    const.SLACK,
    const.DISCORD,
    const.WEIXIN,
    const.WECOM_BOT,
})


@dataclass(frozen=True)
class ChannelInstance:
    """One channel to bring up, with its identity, binding and credentials."""

    instance_id: str
    channel_type: str
    agent_id: str = ""
    credentials: Dict[str, Any] = field(default_factory=dict)
    #: Other Agents sharing this channel's conversations. ``agent_id`` is the
    #: owner/leader that receives every inbound message; ``members`` are the
    #: teammates it may hand work to (via @mention or the agent_delegate tool),
    #: exactly like a Web team conversation. Empty means a solo bot. Owner is
    #: never listed here.
    members: List[str] = field(default_factory=list)
    #: True when synthesized from legacy channel_type rather than an explicit
    #: channel_instances record. Legacy instances carry no credential override.
    legacy: bool = True
    #: Owning tenant for a tenant-owned instance (empty for legacy / team.json
    #: instances). This is the **authoritative anchor** for inbound routing: it
    #: comes from the instance registration row, never from the message or from
    #: whatever Agent the instance happens to be bound to.
    tenant_id: str = ""


def _normalize_type(channel_type: str) -> str:
    if channel_type in (const.WEIXIN, "wx"):
        return const.WEIXIN
    return channel_type


def _parse_channel_type(raw) -> List[str]:
    """Legacy channel_type -> list of type names (string / CSV / list)."""
    if isinstance(raw, list):
        return [str(ch).strip() for ch in raw if str(ch).strip()]
    if isinstance(raw, str):
        return [ch.strip() for ch in raw.split(",") if ch.strip()]
    return []


def _legacy_instances(settings: Mapping[str, Any]) -> List[ChannelInstance]:
    """One instance per legacy channel_type, no credential override.

    Byte-for-byte the pre-multi-instance behavior: instance_id == channel_type,
    credentials read from the global conf() at runtime, routing left to the
    existing config-based AgentRouter (agent_id empty here).
    """
    out: List[ChannelInstance] = []
    for name in _parse_channel_type(settings.get("channel_type", "")):
        ctype = _normalize_type(name)
        out.append(ChannelInstance(instance_id=ctype, channel_type=ctype))
    return out


def _explicit_instances(raw_list: list) -> List[ChannelInstance]:
    out: List[ChannelInstance] = []
    seen_ids = set()
    for index, raw in enumerate(raw_list):
        if not isinstance(raw, Mapping):
            logger.warning(f"[ChannelInstances] entry #{index} is not an object, skipping")
            continue
        channel_type = _normalize_type(str(raw.get("channel_type") or "").strip())
        if not channel_type:
            logger.warning(f"[ChannelInstances] entry #{index} has no channel_type, skipping")
            continue
        instance_id = str(raw.get("instance_id") or "").strip() or channel_type
        if instance_id in seen_ids:
            logger.warning(
                f"[ChannelInstances] duplicate instance_id '{instance_id}', skipping"
            )
            continue
        seen_ids.add(instance_id)

        agent_id = str(raw.get("agent_id") or "").strip()

        raw_members = raw.get("members")
        members: List[str] = []
        if isinstance(raw_members, list):
            for m in raw_members:
                mid = str(m or "").strip()
                # The owner is never a member of its own team, and ids appear
                # once: a duplicate here would show the same teammate twice.
                if mid and mid != agent_id and mid not in members:
                    members.append(mid)

        # Credentials may be given inline under "credentials", or as flat keys
        # on the record itself; only the known keys for this type are kept.
        raw_creds = raw.get("credentials")
        source = raw_creds if isinstance(raw_creds, Mapping) else raw
        creds: Dict[str, Any] = {}
        for key in CREDENTIAL_KEYS.get(channel_type, ()):
            if source.get(key) is not None:
                creds[key] = source.get(key)

        if channel_type not in MULTI_INSTANCE_READY:
            logger.info(
                f"[ChannelInstances] '{channel_type}' does not support multiple "
                f"instances yet; '{instance_id}' will run as a single instance"
            )

        out.append(
            ChannelInstance(
                instance_id=instance_id,
                channel_type=channel_type,
                agent_id=agent_id,
                credentials=creds,
                members=members,
                legacy=False,
            )
        )
    return out


def resolve_channel_instances(
    settings: Mapping[str, Any],
    tenant_instances: Optional[List[ChannelInstance]] = None,
) -> List[ChannelInstance]:
    """Channel instances to run for *settings*.

    Prefers explicit ``channel_instances`` (multi-Agent); otherwise synthesizes
    the legacy set from ``channel_type``. The ``web`` console is intentionally
    not represented here — it is managed separately by the launcher.

    *tenant_instances* are tenant-owned channels resolved from the identity
    store (see :func:`load_tenant_channel_instances`) and are appended to the
    roster list, so one uniform list drives the launcher. An ``instance_id``
    already present in the roster is skipped: the roster is the source of truth
    for a given id, and starting it twice would open two identical connections.
    """
    raw_list = settings.get("channel_instances")
    if isinstance(raw_list, list) and raw_list:
        instances = _explicit_instances(raw_list)
        if not instances:
            logger.warning(
                "[ChannelInstances] channel_instances present but yielded nothing; "
                "falling back to legacy channel_type"
            )
            instances = _legacy_instances(settings)
    else:
        instances = _legacy_instances(settings)

    if not tenant_instances:
        return instances

    seen_ids = {inst.instance_id for inst in instances}
    merged = list(instances)
    for inst in tenant_instances:
        if inst.instance_id in seen_ids:
            logger.warning(
                f"[ChannelInstances] tenant instance '{inst.instance_id}' collides "
                f"with a roster instance of the same id, skipping"
            )
            continue
        seen_ids.add(inst.instance_id)
        merged.append(inst)
    return merged


# ---------------------------------------------------------------------------
# Runtime state for tenant-owned instances.
#
# Restarting a channel is a *process* concern, not durable state: the record
# lives here rather than in the identity store, and is rebuilt by the startup
# synthesis after a restart. It exists so a save can report an honest
# "not applied yet, and here is why" instead of silently leaving the previous
# credentials in service.
# ---------------------------------------------------------------------------

_runtime_state: Dict[str, Dict[str, Any]] = {}
_runtime_state_guard = threading.Lock()

#: One lock per instance id, so two concurrent saves of the same instance cannot
#: start/stop it twice. Created on first use and never removed (a bounded set:
#: one entry per instance the process has touched).
_restart_locks: Dict[str, threading.Lock] = {}
_restart_locks_guard = threading.Lock()


def _instance_restart_lock(instance_id: str) -> threading.Lock:
    with _restart_locks_guard:
        lock = _restart_locks.get(instance_id)
        if lock is None:
            lock = threading.Lock()
            _restart_locks[instance_id] = lock
        return lock


def _record_runtime_state(instance_id: str, *, applied: bool,
                          pending: bool = False, error: str = "") -> Dict[str, Any]:
    state = {"applied": bool(applied), "pending": bool(pending),
             "error": str(error or "")}
    with _runtime_state_guard:
        _runtime_state[instance_id] = state
    return dict(state)


def instance_runtime_state(instance_id: str) -> Dict[str, Any]:
    """The last observed runtime outcome for one instance (never raises)."""
    with _runtime_state_guard:
        state = _runtime_state.get(instance_id)
        return dict(state) if state else {"applied": False, "pending": True, "error": ""}


def _runtime_manager():
    """The process's ChannelManager, or None when this process runs no channels.

    Resolved through the app module the same way the platform channel handlers
    do, so an imported ``channel_instances`` never has to own the manager.
    """
    import sys

    app_module = sys.modules.get("__main__") or sys.modules.get("app")
    return getattr(app_module, "_channel_mgr", None) if app_module else None


def _stop_instance_runtime(instance_id: str) -> None:
    mgr = _runtime_manager()
    if mgr is None:
        return
    try:
        remover = getattr(mgr, "remove_channel", None)
        if callable(remover):
            remover(instance_id)
        else:
            mgr.stop(instance_id)
    except Exception as e:  # stopping is best-effort; the caller reports state
        logger.warning(f"[ChannelInstances] failed to stop '{instance_id}': {e}")


def apply_tenant_instance_runtime(instance_id: str) -> Dict[str, Any]:
    """Make one tenant instance's running state match its stored state, now.

    Called after a create / edit / enable / disable so the change takes effect
    without a maintenance window. Serialized per instance, and it never raises:
    the credential write has already committed, so a runtime failure must be
    *reported* (``applied`` / ``pending`` / ``error``) rather than turned into a
    failed save.

    The old run is always stopped on the way in, including when the new
    credentials cannot be decrypted or started. That is deliberate: leaving the
    previous run up would be "silently keeping the old credential in service",
    which the requirement forbids.

    Identity mode is deliberately not consulted. This is only reachable from a
    tenant-channel write path, which exists in database mode alone, and the row
    lookup below already answers "is there anything to run" honestly; a mode
    check would instead report a stored instance as applied when it never was.
    """
    with _instance_restart_lock(instance_id):
        try:
            from auth.service import get_identity_service

            service = get_identity_service()
            row = service.get_tenant_channel_instance_row(instance_id)
        except Exception as e:
            _stop_instance_runtime(instance_id)
            logger.error(
                f"[ChannelInstances] cannot read instance '{instance_id}': {e}")
            return _record_runtime_state(
                instance_id, applied=False, error=f"instance lookup failed: {e}")

        if row is None or not row.get("active"):
            # Deleted or disabled: nothing should be running under this id.
            _stop_instance_runtime(instance_id)
            return _record_runtime_state(instance_id, applied=True)

        try:
            credentials = service.channel_instance_credentials(
                row["tenant_id"], instance_id)
        except Exception as e:
            _stop_instance_runtime(instance_id)
            logger.error(
                f"[ChannelInstances] instance '{instance_id}' credentials "
                f"unusable, stopping it: {e}")
            return _record_runtime_state(
                instance_id, applied=False,
                error=f"credentials unusable: {getattr(e, 'code', '') or e}")

        inst = ChannelInstance(
            instance_id=instance_id,
            channel_type=_normalize_type(str(row.get("channel_type") or "")),
            agent_id=str(row.get("agent_id") or ""),
            credentials=credentials,
            legacy=False,
            tenant_id=str(row.get("tenant_id") or ""),
        )

        mgr = _runtime_manager()
        if mgr is None:
            # A console-only process (or a test harness) with no channels: the
            # credentials are fine, the effect is simply not observable here.
            return _record_runtime_state(instance_id, applied=False, pending=True)

        # ``restart`` stops the previous run itself, but the guarantee "the old
        # credential is never left serving" must not depend on another object's
        # internal ordering: stop it here first, so a failure to start still
        # leaves nothing running under the previous credentials.
        _stop_instance_runtime(instance_id)
        try:
            mgr.restart(inst)
        except Exception as e:
            logger.error(
                f"[ChannelInstances] instance '{instance_id}' did not start: {e}",
                exc_info=True)
            return _record_runtime_state(
                instance_id, applied=False, error=str(e) or e.__class__.__name__)
        logger.info(f"[ChannelInstances] instance '{instance_id}' applied immediately")
        return _record_runtime_state(instance_id, applied=True)


def load_tenant_channel_instances() -> List[ChannelInstance]:
    """Tenant-owned channel instances to run, from the identity store.

    Thin adapter over the identity layer, imported lazily so ``channel/`` never
    hard-depends on ``auth`` at import time (``channel/external_identity.py``
    does the same). Returns ``[]`` outside database mode.

    This runs during startup, *before* the Web console serves. A failure to
    read the store — or a single instance whose credential cannot be decrypted
    — is therefore logged and degraded, never fatal: the remaining channels and
    the console still come up.
    """
    from channel.external_identity import is_database_mode

    if not is_database_mode():
        return []

    try:
        from auth.service import get_identity_service

        service = get_identity_service()
        rows = service.list_enabled_tenant_channel_instances()
    except Exception as e:
        logger.error(
            f"[ChannelInstances] cannot read tenant channel instances; starting "
            f"without them: {e}"
        )
        return []

    out: List[ChannelInstance] = []
    for row in rows:
        instance_id = str(row.get("id") or "")
        tenant_id = str(row.get("tenant_id") or "")
        try:
            credentials = service.channel_instance_credentials(tenant_id, instance_id)
        except Exception as e:
            logger.error(
                f"[ChannelInstances] tenant instance '{instance_id}' (tenant "
                f"'{tenant_id}') has unusable credentials, skipping: {e}"
            )
            continue
        out.append(
            ChannelInstance(
                instance_id=instance_id,
                channel_type=_normalize_type(str(row.get("channel_type") or "")),
                agent_id=str(row.get("agent_id") or ""),
                credentials=credentials,
                legacy=False,
                # Ownership comes from the registration row, so inbound routing
                # never has to infer a tenant from the bound Agent.
                tenant_id=tenant_id,
            )
        )
    if out:
        logger.info(
            f"[ChannelInstances] loaded {len(out)} tenant-owned channel instance(s)"
        )
    return out


#: Display labels for the channel types a tenant may own, kept beside
#: ``CREDENTIAL_KEYS`` so the console never has to carry a second copy of the
#: field contract. A type missing here still works (its code is shown).
TENANT_CHANNEL_LABELS: Dict[str, Dict[str, str]] = {
    const.FEISHU: {"zh": "飞书", "en": "Feishu"},
    const.DINGTALK: {"zh": "钉钉", "en": "DingTalk"},
    const.WECOM_BOT: {"zh": "企微智能机器人", "en": "WeCom Bot"},
    const.WEIXIN: {"zh": "微信", "en": "WeChat"},
    const.QQ: {"zh": "QQ 机器人", "en": "QQ Bot"},
    const.TELEGRAM: {"zh": "Telegram", "en": "Telegram"},
    const.SLACK: {"zh": "Slack", "en": "Slack"},
    const.DISCORD: {"zh": "Discord", "en": "Discord"},
}

#: Icon + colour per channel type, so the console renders the tenant cards with
#: the same appearance as the platform cards without keeping a second copy of
#: this mapping in JavaScript. A type missing here still renders (generic icon).
TENANT_CHANNEL_APPEARANCE: Dict[str, Dict[str, str]] = {
    const.FEISHU: {"icon": "fa-paper-plane", "color": "blue"},
    const.DINGTALK: {"icon": "fa-comments", "color": "blue"},
    const.WECOM_BOT: {"icon": "fa-robot", "color": "emerald"},
    const.WEIXIN: {"icon": "fa-comment", "color": "emerald"},
    const.QQ: {"icon": "fa-comment", "color": "blue"},
    const.TELEGRAM: {"icon": "fa-paper-plane", "color": "sky"},
    const.SLACK: {"icon": "fa-hashtag", "color": "purple"},
    const.DISCORD: {"icon": "fa-discord", "color": "indigo"},
}

#: Credential field labels for the tenant form. A key missing here falls back to
#: its own name, so adding a key to CREDENTIAL_KEYS never breaks the form.
CREDENTIAL_FIELD_LABELS: Dict[str, Dict[str, str]] = {
    "feishu_app_id": {"zh": "App ID", "en": "App ID"},
    "feishu_app_secret": {"zh": "App Secret", "en": "App Secret"},
    "feishu_token": {"zh": "校验 Token（可选）", "en": "Verification Token (optional)"},
    "feishu_bot_name": {"zh": "机器人名称（可选）", "en": "Bot name (optional)"},
    "dingtalk_client_id": {"zh": "Client ID", "en": "Client ID"},
    "dingtalk_client_secret": {"zh": "Client Secret", "en": "Client Secret"},
    "dingtalk_robot_code": {"zh": "机器人编码", "en": "Robot code"},
    "wecom_bot_id": {"zh": "Bot ID", "en": "Bot ID"},
    "wecom_bot_secret": {"zh": "Secret", "en": "Secret"},
    "wecom_bot_token": {"zh": "Token", "en": "Token"},
    "wecom_bot_encoding_aes_key": {"zh": "EncodingAESKey", "en": "EncodingAESKey"},
    "weixin_token": {"zh": "Token", "en": "Token"},
    "weixin_base_url": {"zh": "回调基址", "en": "Callback base URL"},
    "qq_app_id": {"zh": "App ID", "en": "App ID"},
    "qq_app_secret": {"zh": "App Secret", "en": "App Secret"},
    "telegram_token": {"zh": "Bot Token", "en": "Bot Token"},
    "slack_bot_token": {"zh": "Bot Token (xoxb-)", "en": "Bot Token (xoxb-)"},
    "slack_app_token": {"zh": "App Token (xapp-)", "en": "App Token (xapp-)"},
    "discord_token": {"zh": "Bot Token", "en": "Bot Token"},
}

#: Credential keys whose value must never be echoed back to a client.
_SECRET_KEY_HINTS = ("secret", "token", "aes", "key", "password")


def tenant_channel_types() -> List[Dict[str, Any]]:
    """Channel types a tenant may own, with the fields its form must collect.

    This mirrors the server-side gate exactly — a type is offered only when it
    is both multi-instance ready and has declared credential keys — so the
    console cannot present a type that ``create_tenant_channel_instance`` would
    then reject. Labels are display-only; the credential *keys* are the contract
    (the server rejects any field outside them).
    """
    out: List[Dict[str, Any]] = []
    for channel_type in sorted(MULTI_INSTANCE_READY):
        keys = CREDENTIAL_KEYS.get(channel_type) or ()
        if not keys:
            continue
        required = set(REQUIRED_CREDENTIAL_KEYS.get(channel_type) or ())
        out.append({
            "channel_type": channel_type,
            "label": TENANT_CHANNEL_LABELS.get(
                channel_type, {"zh": channel_type, "en": channel_type}),
            # Appearance is presentation-only, but keeping it server-side stops
            # the console from carrying a second copy that drifts from this one.
            "icon": (TENANT_CHANNEL_APPEARANCE.get(channel_type) or {}).get(
                "icon", "fa-tower-broadcast"),
            "color": (TENANT_CHANNEL_APPEARANCE.get(channel_type) or {}).get(
                "color", "primary"),
            "credential_fields": [
                {
                    "key": key,
                    "label": CREDENTIAL_FIELD_LABELS.get(
                        key, {"zh": key, "en": key}),
                    # Secret-looking fields render as password inputs and are
                    # never sent back down; the bundle is write-only.
                    "secret": any(hint in key for hint in _SECRET_KEY_HINTS),
                    # Required-ness travels with the same declaration the server
                    # validates against, so the console never keeps a second
                    # copy of the minimum set that could drift from this one.
                    "required": key in required,
                }
                for key in keys
            ],
        })
    return out


# ---------------------------------------------------------------------------
# Persistence helpers for the explicit multi-instance list.
#
# These edit the ``channel_instances`` array in the roster file (team.json) and
# leave the legacy ``channel_type`` + flat credentials path completely alone, so
# an install that never opts into multiple instances is never touched.
# ---------------------------------------------------------------------------

def _filtered_credentials(channel_type: str, source: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only the credential keys meaningful for *channel_type*."""
    ctype = _normalize_type((channel_type or "").strip())
    creds: Dict[str, Any] = {}
    for key in CREDENTIAL_KEYS.get(ctype, ()):
        value = source.get(key)
        if value is not None:
            creds[key] = value
    return creds


def bootstrap_legacy_instances(
    settings: Mapping[str, Any],
    roster: Mapping[str, Any],
    default_agent_id: str = "",
) -> List[Dict[str, Any]]:
    """Fold a legacy single-channel setup into ``channel_instances`` records.

    Called when an install first crosses into multi-Agent territory (team.json
    is being written for the first time). A legacy install keeps its channel
    credentials as flat keys in config.json and names the channel in
    ``channel_type``; multi-Agent mode routes entirely off ``channel_instances``,
    so those flat channels would silently go dark unless carried over.

    For every multi-instance-ready channel named in ``channel_type`` that has
    credentials in ``settings`` and no existing record, synthesize one record
    bound to the default Agent (``instance_id == channel_type``, matching the
    legacy id so nothing else has to change). The config.json flat keys are left
    in place untouched — multi-Agent startup simply ignores them.

    Returns the (possibly extended) channel_instances list.
    """
    records = [
        dict(item)
        for item in (roster.get("channel_instances") or [])
        if isinstance(item, Mapping)
    ]
    have_types = {
        _normalize_type(str(r.get("channel_type") or "").strip()) for r in records
    }
    default_id = (default_agent_id or roster.get("default_agent_id") or "").strip()

    for name in _parse_channel_type(settings.get("channel_type", "")):
        ctype = _normalize_type(name)
        if ctype not in MULTI_INSTANCE_READY or ctype in have_types:
            continue
        creds = _filtered_credentials(ctype, settings)
        if not creds:
            continue
        records.append(
            {
                "instance_id": ctype,
                "channel_type": ctype,
                "agent_id": default_id,
                "credentials": creds,
            }
        )
        have_types.add(ctype)
        # Weixin's scan-login token lives in a credentials file, not config.json.
        # The bootstrapped instance (instance_id == "weixin") reads a per-instance
        # file, so carry the legacy default file over to it — otherwise the user
        # would have to re-scan just because they added an Agent.
        if ctype == const.WEIXIN:
            _carry_weixin_credentials_file(ctype)
        logger.info(
            f"[ChannelInstances] bootstrapped legacy '{ctype}' credentials into a "
            f"channel_instances record bound to '{default_id or 'default'}'"
        )
    return records


def _carry_weixin_credentials_file(instance_id: str) -> None:
    """Copy the legacy Weixin token file to the per-instance path, once.

    Best-effort and idempotent: if the legacy default file exists and the
    per-instance file does not yet, the token (and persisted context tokens) are
    copied so the bootstrapped instance stays logged in. Never overwrites an
    existing per-instance file, and never raises into the write path.
    """
    try:
        import shutil
        from config import get_weixin_credentials_path

        legacy = get_weixin_credentials_path()
        target = get_weixin_credentials_path(instance_id)
        if legacy == target:
            return
        if os.path.exists(legacy) and not os.path.exists(target):
            shutil.copy2(legacy, target)
            logger.info(
                f"[ChannelInstances] carried Weixin credentials '{legacy}' -> "
                f"'{target}' so the bootstrapped instance stays logged in"
            )
    except Exception as e:
        logger.warning(f"[ChannelInstances] Weixin credentials carry-over skipped: {e}")


def read_raw_instances(settings: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The raw ``channel_instances`` records as stored, or an empty list."""
    from agent import team

    roster = team.read(settings)
    raw = roster.get("channel_instances")
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def _clean_members(members, owner_id: str) -> list:
    """Normalize a members list: strings, de-duped, no owner, order preserved."""
    out: list = []
    owner = (owner_id or "").strip()
    for m in members or []:
        mid = str(m or "").strip()
        if mid and mid != owner and mid not in out:
            out.append(mid)
    return out


def upsert_instance(
    settings: Mapping[str, Any],
    channel_type: str,
    instance_id: str = "",
    agent_id: Optional[str] = None,
    credentials: Optional[Mapping[str, Any]] = None,
    members: Optional[list] = None,
) -> ChannelInstance:
    """Create or update one channel instance record and persist it.

    Matching is by ``instance_id``. When it is empty a new stable id is
    generated. ``agent_id``, ``credentials`` and ``members`` are merged onto any
    existing record so a partial update (e.g. credentials only) does not drop
    the binding or the team. Pass ``members=None`` to leave the team untouched,
    or ``members=[]`` to clear it. Returns the resulting :class:`ChannelInstance`.
    """
    from agent import team

    ctype = _normalize_type((channel_type or "").strip())
    records = read_raw_instances(settings)
    taken = {str(r.get("instance_id") or "").strip() for r in records}

    target_id = (instance_id or "").strip()
    if not target_id:
        target_id = new_instance_id(ctype, taken)

    incoming_creds = _filtered_credentials(ctype, credentials or {})

    updated = False
    for record in records:
        if str(record.get("instance_id") or "").strip() != target_id:
            continue
        record["channel_type"] = ctype
        if agent_id is not None:
            record["agent_id"] = str(agent_id).strip()
        if incoming_creds:
            merged = dict(record.get("credentials") or {})
            merged.update(incoming_creds)
            record["credentials"] = merged
        if members is not None:
            cleaned = _clean_members(members, record.get("agent_id") or "")
            if cleaned:
                record["members"] = cleaned
            else:
                record.pop("members", None)
        updated = True
        result_record = record
        break

    if not updated:
        result_record = {
            "instance_id": target_id,
            "channel_type": ctype,
            "agent_id": str(agent_id).strip() if agent_id is not None else "",
            "credentials": incoming_creds,
        }
        cleaned = _clean_members(members, result_record["agent_id"])
        if cleaned:
            result_record["members"] = cleaned
        records.append(result_record)

    roster = team.read(settings)
    roster["channel_instances"] = records
    team.write(settings, roster)

    return ChannelInstance(
        instance_id=target_id,
        channel_type=ctype,
        agent_id=str(result_record.get("agent_id") or ""),
        credentials=dict(result_record.get("credentials") or {}),
        members=list(result_record.get("members") or []),
        legacy=False,
    )


def remove_instance(settings: Mapping[str, Any], instance_id: str) -> bool:
    """Drop one instance record by id. Returns True if something was removed."""
    from agent import team

    target_id = (instance_id or "").strip()
    if not target_id:
        return False
    records = read_raw_instances(settings)
    kept = [r for r in records if str(r.get("instance_id") or "").strip() != target_id]
    if len(kept) == len(records):
        return False
    roster = team.read(settings)
    roster["channel_instances"] = kept
    team.write(settings, roster)
    return True


def get_instance(settings: Mapping[str, Any], instance_id: str) -> Optional[ChannelInstance]:
    """Resolve one instance by id, or None.

    Overlays the roster file first so the lookup works even when the caller
    passes bare ``conf()`` (channel_instances lives in team.json, not config).
    """
    from agent import team

    target_id = (instance_id or "").strip()
    resolved = team.resolve(settings)
    for inst in resolve_channel_instances(resolved):
        if inst.instance_id == target_id:
            return inst
    return None
