import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Tuple

# SKILL root = 3 levels up from this file (scripts/lib/config.py -> scripts/lib -> scripts -> skill root)
# Resolved at import time so it works regardless of the caller's CWD.
_SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILL_DOTENV = _SKILL_ROOT / ".env"

CONFIG_DIR = Path.home() / ".sap-adt-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"

_OLD_CONFIG_DIR = Path.home() / ".sap-abap-cli"
_OLD_CONFIG_FILE = _OLD_CONFIG_DIR / "config.json"

SETUP_GUIDE = """\
SAP credentials not configured.

Credential lookup order (highest priority first):
  1. Process environment variables
  2. .env file in the SKILL directory ({skill_dotenv})
  3. ~/.sap-adt-cli/config.json

To set up your SAP connection, run:
  python3 sap_adt_cli.py configure

Or create a SKILL-local .env file / set environment variables:
  SAP_URL        - SAP system URL (e.g. https://my-sap.example.com:8000)
  SAP_USERNAME   - SAP username
  SAP_PASSWORD   - SAP password
  SAP_CLIENT     - SAP client number (e.g. 100)

Optional:
  SAP_LANGUAGE   - Language code (default: EN)
  SAP_VERIFY_SSL - Set to 0 to disable SSL verification (default: 1)
  SAP_ALLOW_WRITE - Set to 1 to enable source write commands (default: 0)
  SAP_ALLOW_TRANSPORT - Set to 1 to enable transport write commands (default: 0)
""".format(skill_dotenv=_SKILL_DOTENV)


@dataclass
class SapConfig:
    url: str
    username: str
    password: str
    client: str
    language: str = "EN"
    verify_ssl: bool = True
    allow_write: bool = False
    allow_transport: bool = False

    def base_url(self) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(self.url.rstrip("/"))
        return f"{parsed.scheme}://{parsed.netloc}"


def _load_skill_dotenv() -> dict:
    if not _SKILL_DOTENV.exists():
        return {}
    result = {}
    with open(_SKILL_DOTENV) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            if key:
                result[key] = value
    return result


def _env_value(key: str, dotenv: dict, default: str = None) -> Optional[str]:
    return os.getenv(key) or dotenv.get(key) or default


def _env_bool(key: str, dotenv: dict, default: bool = False) -> bool:
    value = _env_value(key, dotenv)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_source(required_keys: list, dotenv: dict) -> str:
    env_keys = [key for key in required_keys if os.getenv(key)]
    dotenv_keys = [key for key in required_keys if not os.getenv(key) and dotenv.get(key)]
    if env_keys and dotenv_keys:
        return f"process environment + {_SKILL_DOTENV}"
    if env_keys:
        return "process environment"
    return str(_SKILL_DOTENV)


def load_config_with_source() -> Tuple[Optional[SapConfig], Optional[str]]:
    dotenv = _load_skill_dotenv()
    required_keys = ["SAP_URL", "SAP_USERNAME", "SAP_PASSWORD", "SAP_CLIENT"]

    url = _env_value("SAP_URL", dotenv)
    username = _env_value("SAP_USERNAME", dotenv)
    password = _env_value("SAP_PASSWORD", dotenv)
    client = _env_value("SAP_CLIENT", dotenv)

    if url and username and password and client:
        source_keys = required_keys + [
            "SAP_LANGUAGE",
            "SAP_VERIFY_SSL",
            "SAP_ALLOW_WRITE",
            "SAP_ALLOW_TRANSPORT",
        ]
        return SapConfig(
            url=url,
            username=username,
            password=password,
            client=client,
            language=_env_value("SAP_LANGUAGE", dotenv, "EN"),
            verify_ssl=_env_value("SAP_VERIFY_SSL", dotenv, "1") != "0",
            allow_write=_env_bool("SAP_ALLOW_WRITE", dotenv),
            allow_transport=_env_bool("SAP_ALLOW_TRANSPORT", dotenv),
        ), _env_source(source_keys, dotenv)

    if not CONFIG_FILE.exists() and _OLD_CONFIG_FILE.exists():
        try:
            import shutil
            CONFIG_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copy2(_OLD_CONFIG_FILE, CONFIG_FILE)
            CONFIG_FILE.chmod(0o600)
            print(
                f"Config migrated: {_OLD_CONFIG_FILE} → {CONFIG_FILE}\n"
                f"Old directory {_OLD_CONFIG_DIR}/ can be removed manually.",
                file=sys.stderr,
            )
        except Exception as e:
            print(
                f"WARNING: Could not migrate config from {_OLD_CONFIG_FILE}: {e}\n"
                f"Run `sap-adt-cli configure` to set up credentials.",
                file=sys.stderr,
            )

    if not CONFIG_FILE.exists():
        return None, None

    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return SapConfig(
            url=data["url"],
            username=data["username"],
            password=data["password"],
            client=data["client"],
            language=data.get("language", "EN"),
            verify_ssl=data.get("verify_ssl", True),
            allow_write=data.get("allow_write", False),
            allow_transport=data.get("allow_transport", False),
        ), str(CONFIG_FILE)
    except (json.JSONDecodeError, KeyError):
        return None, None


def load_config() -> Optional[SapConfig]:
    config, _ = load_config_with_source()
    return config


def save_config(config: SapConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(config), f, indent=2)


def get_config() -> SapConfig:
    config = load_config()
    if config is None:
        print(SETUP_GUIDE, file=sys.stderr)
        sys.exit(1)
    return config


def save_config_from_flags(
    url: Optional[str],
    username: Optional[str],
    password: Optional[str],
    client: Optional[str],
    language: Optional[str] = None,
    verify_ssl: bool = True,
    allow_write: bool = False,
    allow_transport: bool = False,
) -> SapConfig:
    existing = load_config()
    resolved_url = url or getattr(existing, "url", None)
    resolved_username = username or getattr(existing, "username", None)
    resolved_password = password or getattr(existing, "password", None)
    resolved_client = client or getattr(existing, "client", None)
    resolved_language = language or getattr(existing, "language", None) or "EN"

    missing = [name for name, val in [
        ("--url", resolved_url),
        ("--username", resolved_username),
        ("--password", resolved_password),
        ("--client", resolved_client),
    ] if not val]

    if missing:
        print(f"Error: missing required fields: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    config = SapConfig(
        url=resolved_url,
        username=resolved_username,
        password=resolved_password,
        client=resolved_client,
        language=resolved_language,
        verify_ssl=verify_ssl,
        allow_write=allow_write,
        allow_transport=allow_transport,
    )
    save_config(config)
    print(f"Configuration saved to {CONFIG_FILE}")
    print("Warning: credentials are stored in plain text. Ensure this file remains private (permissions: 600).")
    return config


def run_configure_wizard() -> SapConfig:
    existing = load_config()

    def _prompt(label: str, default: Optional[str] = None, secret: bool = False) -> str:
        hint = f" [{default}]" if default else ""
        prompt_text = f"{label}{hint}: "
        if secret:
            import getpass
            value = getpass.getpass(prompt_text)
        else:
            value = input(prompt_text)
        return value.strip() or (default or "")

    print("\nSAP ADT CLI — Connection Setup")
    print("=" * 40)

    url = _prompt("SAP System URL (e.g. https://my-sap.example.com:8000)", getattr(existing, "url", None))
    username = _prompt("SAP Username", getattr(existing, "username", None))
    password = _prompt("SAP Password", secret=True)
    client = _prompt("SAP Client (e.g. 100)", getattr(existing, "client", None))
    language = _prompt("Language code", getattr(existing, "language", "EN") or "EN")
    verify_ssl_raw = _prompt(
        "Verify SSL certificate? (y/n)",
        "y" if getattr(existing, "verify_ssl", True) else "n",
    )
    verify_ssl = verify_ssl_raw.lower() not in ("n", "no", "0", "false")

    allow_write_raw = _prompt(
        "Enable source code write (write-source, activate)? [y/N]",
        "y" if getattr(existing, "allow_write", False) else "N",
    )
    allow_write = allow_write_raw.lower() in ("y", "yes")

    allow_transport_raw = _prompt(
        "Enable transport write operations (create-transport, release-transport)? [y/N]",
        "y" if getattr(existing, "allow_transport", False) else "N",
    )
    allow_transport = allow_transport_raw.lower() in ("y", "yes")

    config = SapConfig(
        url=url,
        username=username,
        password=password,
        client=client,
        language=language or "EN",
        verify_ssl=verify_ssl,
        allow_write=allow_write,
        allow_transport=allow_transport,
    )
    save_config(config)
    print(f"\nConfiguration saved to {CONFIG_FILE}")
    return config
