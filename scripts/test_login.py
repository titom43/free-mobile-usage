#!/usr/bin/env python3
"""Smoke test for the structured Free Mobile iOS API.

Run from the repository root:
  FREEMOBILE_USERNAME=... FREEMOBILE_PASSWORD=... python scripts/test_login.py

On the first login, Free may request its temporary SMS code.  Later runs first
try the locally persisted access token, then login with the trusted-device UUID
before ever asking for another code.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from getpass import getpass
from pathlib import Path

import aiohttp

from mobile_api_client import (
    FreeMobileApiAuthError,
    FreeMobileApiError,
    FreeMobileApiMfaRequired,
    FreeMobileMobileApiClient,
    TokenState,
)

ROOT = Path(__file__).resolve().parents[1]


def _prompt_otp_code() -> str:
    """Request a code twice without displaying either entry."""
    for attempt in range(3):
        first = getpass("Code SMS Free Mobile (saisie masquee): ").strip()
        if not first:
            return ""
        second = getpass("Confirmer le code SMS (saisie masquee): ").strip()
        if first == second:
            return first
        if attempt < 2:
            print("Les deux codes ne correspondent pas. Nouvel essai.", file=sys.stderr)
    return ""


def _line_summary(line: dict[str, object], usage: dict[str, object]) -> dict[str, object]:
    identity = line.get("identity") if isinstance(line.get("identity"), dict) else {}
    return {
        "name": identity.get("firstName"),
        "phone_number": line.get("msisdn"),
        **usage,
    }


def _load_cookie_jar(path: Path) -> aiohttp.CookieJar:
    """Restore API cookies when Free ties a token to its web session."""
    jar = aiohttp.CookieJar()
    if not path.exists():
        return jar
    try:
        jar.load(path)
    except (OSError, ValueError):
        # A stale or incompatible cookie jar is never a reason to discard the
        # token or trusted-device state.
        pass
    return jar


async def main() -> int:
    username = os.environ.get("FREEMOBILE_USERNAME")
    password = os.environ.get("FREEMOBILE_PASSWORD")
    requested_line = os.environ.get("FREEMOBILE_LINE_ID")
    token_path = Path(
        os.environ.get("FREEMOBILE_TOKEN_STORE", ROOT / "private" / "free_mobile_mobile_tokens.json")
    )
    cookie_path = Path(
        os.environ.get("FREEMOBILE_COOKIE_JAR", ROOT / "private" / "free_mobile_mobile_cookies.jar")
    )

    if (not username or not password) and not token_path.exists():
        print("Set FREEMOBILE_USERNAME and FREEMOBILE_PASSWORD for the first run.", file=sys.stderr)
        return 2

    try:
        import certifi
    except ImportError:
        connector = None
    else:
        connector = aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))

    state = TokenState.load(token_path)
    cookie_jar = _load_cookie_jar(cookie_path)
    async with aiohttp.ClientSession(connector=connector, cookie_jar=cookie_jar) as session:
        client = FreeMobileMobileApiClient(session, state)
        try:
            account_login = await client.async_authenticate(username, password)
        except FreeMobileApiMfaRequired as challenge:
            code = _prompt_otp_code()
            if not code:
                print("Connexion annulee : code SMS absent.", file=sys.stderr)
                return 3
            await client.async_complete_mfa(username or state.account_login or "", challenge, code)
            account_login = username or state.account_login
            if not account_login:
                raise FreeMobileApiAuthError("Unable to identify the main Free Mobile line")

        subscriber = await client.async_get_subscriber(account_login)
        lines = subscriber.get("lines", [])
        if not isinstance(lines, list):
            raise FreeMobileApiAuthError("Free Mobile did not return the family line list")

        result_lines = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            line_login = line.get("login")
            if not isinstance(line_login, str):
                continue
            if requested_line and line_login != requested_line:
                continue
            usage = await client.async_get_usage(line_login)
            result_lines.append(_line_summary(line, usage))

    state.save(token_path)
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_jar.save(cookie_path)
    cookie_path.chmod(0o600)
    print(json.dumps({"authentication": "ok", "lines": result_lines}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except FreeMobileApiError as err:
        print(str(err), file=sys.stderr)
        raise SystemExit(4) from err
