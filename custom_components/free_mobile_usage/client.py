"""Minimal async client for Free Mobile subscriber usage.

Free Mobile does not publish an official usage API for Home Assistant. This
client intentionally keeps login/fetch/parsing isolated so portal changes are
contained in this file.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .models import FreeMobileUsageData

BASE_URL = "https://mobile.free.fr"
ACCOUNT_URL = f"{BASE_URL}/account/v2"
CSRF_URL = f"{ACCOUNT_URL}/api/auth/csrf"
CREDENTIALS_URL = f"{ACCOUNT_URL}/api/auth/callback/credentials"
CHANGE_USER_ACTION = "7f0863763ca78f33949868b59a6087439c1ad779ba"
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


class FreeMobileUsageError(Exception):
    """Base exception for Free Mobile usage errors."""


class FreeMobileAuthError(FreeMobileUsageError):
    """Authentication failed."""


class FreeMobileMfaRequiredError(FreeMobileUsageError):
    """A temporary security code is required by Free Mobile."""


class FreeMobileClient:
    """Client able to fetch Free Mobile usage pages."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        line_id: str | None = None,
        mobile: bool = False,
        otp_code: str | None = None,
        trust_device: bool = True,
    ) -> None:
        self.username = username
        self.password = password
        self.session = session
        self.line_id = line_id
        self.mobile = mobile
        self.otp_code = otp_code
        self.trust_device = trust_device
        self._last_csrf_token: str | None = None

    async def async_get_usage(self, data_limit_gb: float | None = None) -> FreeMobileUsageData:
        """Fetch and parse usage data."""
        html = await self._async_fetch_usage_payload()
        parsed = parse_usage_html(html, fallback_data_limit_gb=data_limit_gb)
        return FreeMobileUsageData(
            account_name=parsed.account_name,
            phone_number=parsed.phone_number,
            data_used_gb=parsed.data_used_gb,
            data_limit_gb=parsed.data_limit_gb,
            national_data_used_gb=parsed.national_data_used_gb,
            national_data_limit_gb=parsed.national_data_limit_gb,
            roaming_data_used_gb=parsed.roaming_data_used_gb,
            roaming_data_limit_gb=parsed.roaming_data_limit_gb,
            out_of_plan_eur=parsed.out_of_plan_eur,
            voice_used=parsed.voice_used,
            sms_used=parsed.sms_used,
            mms_used=parsed.mms_used,
            next_reset_date=parsed.next_reset_date,
            raw_summary=parsed.raw_summary,
            fetched_at=parsed.fetched_at,
        )

    async def async_complete_mfa(self, otp_code: str, data_limit_gb: float | None = None) -> FreeMobileUsageData:
        """Complete an in-progress MFA challenge and parse usage data."""
        if not self._last_csrf_token:
            raise FreeMobileMfaRequiredError("No Free Mobile MFA challenge is in progress")

        self.otp_code = otp_code
        await self._async_submit_otp(self._last_csrf_token)
        await self._async_get_text(ACCOUNT_URL)
        payload = await self._async_get_text(f"{ACCOUNT_URL}?_rsc=freeMobileUsage")
        if _looks_like_mfa_page(payload):
            raise FreeMobileMfaRequiredError("Free Mobile still requires a temporary security code")
        if _looks_like_login_page(payload):
            raise FreeMobileAuthError("Free Mobile session was not established after MFA")
        parsed = parse_usage_html(payload, fallback_data_limit_gb=data_limit_gb)
        return FreeMobileUsageData(
            account_name=parsed.account_name,
            phone_number=parsed.phone_number,
            data_used_gb=parsed.data_used_gb,
            data_limit_gb=parsed.data_limit_gb,
            national_data_used_gb=parsed.national_data_used_gb,
            national_data_limit_gb=parsed.national_data_limit_gb,
            roaming_data_used_gb=parsed.roaming_data_used_gb,
            roaming_data_limit_gb=parsed.roaming_data_limit_gb,
            out_of_plan_eur=parsed.out_of_plan_eur,
            voice_used=parsed.voice_used,
            sms_used=parsed.sms_used,
            mms_used=parsed.mms_used,
            next_reset_date=parsed.next_reset_date,
            raw_summary=parsed.raw_summary,
            fetched_at=parsed.fetched_at,
        )

    async def _async_fetch_usage_payload(self) -> str:
        """Login and return the account RSC payload/HTML."""
        if self.line_id:
            await self._async_select_line()

        cached_payload = await self._async_get_text(f"{ACCOUNT_URL}?_rsc=freeMobileUsage")
        if not _looks_like_login_page(cached_payload) and not _looks_like_mfa_page(cached_payload):
            return cached_payload

        if not self.username or not self.password:
            raise FreeMobileAuthError("Free Mobile credentials are required when the cached session is not valid")

        csrf_token = await self._async_get_csrf_token()
        self._last_csrf_token = csrf_token
        login_url = await self._async_login(csrf_token)

        if self.line_id:
            login_url = f"{ACCOUNT_URL}/login?login={self.line_id}"

        # The portal is a Next.js app. After credentials auth it returns a
        # /login?login=<line_id> URL that selects the line before loading /account/v2.
        await self._async_get_text(login_url)
        if self.line_id:
            await self._async_select_line()
        else:
            await self._async_post_text(login_url, data="[]")

        payload = await self._async_get_text(f"{ACCOUNT_URL}?_rsc=freeMobileUsage")
        if _looks_like_mfa_page(payload):
            if self.otp_code:
                await self._async_submit_otp(csrf_token)
                await self._async_get_text(ACCOUNT_URL)
                payload = await self._async_get_text(f"{ACCOUNT_URL}?_rsc=freeMobileUsage")
                if not _looks_like_mfa_page(payload) and not _looks_like_login_page(payload):
                    return payload
            raise FreeMobileMfaRequiredError(
                "Free Mobile requires a temporary security code."
            )
        if _looks_like_login_page(payload):
            raise FreeMobileAuthError("Free Mobile session was not established")
        return payload

    async def _async_select_line(self) -> None:
        """Select a family line in the current Free Mobile session."""
        if not self.line_id:
            return

        headers = {
            **self._headers,
            "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": ACCOUNT_URL,
            "next-action": CHANGE_USER_ACTION,
        }
        async with self.session.post(
            ACCOUNT_URL,
            data=f'["{self.line_id}"]',
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            response.raise_for_status()
            await response.text()

    async def _async_get_csrf_token(self) -> str:
        async with self.session.get(CSRF_URL, headers=self._headers, timeout=DEFAULT_TIMEOUT) as response:
            response.raise_for_status()
            payload = await response.json()
        token = payload.get("csrfToken")
        if not token:
            raise FreeMobileUsageError("Missing Free Mobile CSRF token")
        return str(token)

    async def _async_login(self, csrf_token: str) -> str:
        payload = {
            "username": self.username,
            "password": self.password,
            "redirect": "false",
            "csrfToken": csrf_token,
            "callbackUrl": f"{ACCOUNT_URL}/login",
            "json": "true",
        }
        headers = {**self._headers, "Content-Type": "application/x-www-form-urlencoded"}
        async with self.session.post(CREDENTIALS_URL, data=payload, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 401:
                raise FreeMobileAuthError("Invalid Free Mobile credentials")
            response.raise_for_status()
            body = await response.json()

        url = body.get("url")
        if not url:
            raise FreeMobileUsageError("Free Mobile login did not return a redirect URL")
        if "error=" in str(url):
            raise FreeMobileAuthError(str(url))
        return urljoin(BASE_URL, str(url))

    async def _async_submit_otp(self, csrf_token: str) -> str:
        """Submit Free Mobile temporary SMS code for the current login session."""
        if not self.otp_code:
            raise FreeMobileMfaRequiredError("Missing Free Mobile temporary security code")

        payload = {
            "codeOtp": self.otp_code,
            "redirect": "false",
            "isTrusted": "true" if self.trust_device else "false",
            "csrfToken": csrf_token,
            "callbackUrl": f"{ACCOUNT_URL}/otp",
            "json": "true",
        }
        headers = {**self._headers, "Content-Type": "application/x-www-form-urlencoded"}
        async with self.session.post(CREDENTIALS_URL, data=payload, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 401:
                raise FreeMobileAuthError("Invalid Free Mobile temporary security code")
            response.raise_for_status()
            body = await response.json()

        url = str(body.get("url", ""))
        if not url:
            raise FreeMobileUsageError("Free Mobile MFA did not return a redirect URL")
        parsed = urlparse(url)
        if parsed.query and "error=" in parsed.query:
            raise FreeMobileAuthError(url)
        return urljoin(BASE_URL, url)

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        }
        if self.mobile:
            headers["User-Agent"] = (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            )
        return headers

    async def _async_get_text(self, url: str) -> str:
        async with self.session.get(url, headers=self._headers, timeout=DEFAULT_TIMEOUT) as response:
            response.raise_for_status()
            return await response.text()

    async def _async_post_text(self, url: str, data: str) -> str:
        headers = {**self._headers, "Content-Type": "text/plain;charset=UTF-8"}
        async with self.session.post(url, data=data, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
            response.raise_for_status()
            return await response.text()


def _looks_like_login_page(payload: str) -> bool:
    """Return true if the returned page still looks like a login page."""
    lowered = payload.lower()
    if "api/auth/callback/credentials" in lowered and "csrf" in lowered:
        return True
    text = _strip_accents(_normalize_payload_text(payload).lower())
    return "identifiant" in text and "mot de passe" in text and "connexion" in text


def _looks_like_mfa_page(payload: str) -> bool:
    """Return true if Free asks for its temporary security code."""
    lowered = _normalize_payload_text(payload).lower()
    return "code de securite" in _strip_accents(lowered) or "code de sécurité" in lowered


def _strip_accents(value: str) -> str:
    replacements = str.maketrans({"é": "e", "è": "e", "ê": "e", "à": "a", "ù": "u", "ç": "c"})
    return value.translate(replacements)


def parse_usage_html(html: str, fallback_data_limit_gb: float | None = None) -> FreeMobileUsageData:
    """Parse usage data from a Free Mobile HTML/RSC payload."""
    text = _normalize_payload_text(html)
    national_used_gb, national_limit_gb = _extract_section_internet_pair_gb(html, "En France")
    roaming_used_gb, roaming_limit_gb = _extract_section_internet_pair_gb(html, "À l'étranger")

    data_used_gb = national_used_gb
    data_limit_gb = national_limit_gb if national_limit_gb is not None else fallback_data_limit_gb

    roaming_out_of_plan = _extract_section_euros(html, "À l'étranger")
    out_of_plan = roaming_out_of_plan if roaming_out_of_plan is not None else _extract_euros(text)

    return FreeMobileUsageData(
        account_name=_first_match(
            text,
            [
                r"Bonjour\s+(.+?)\s+Bienvenue",
                r"Ligne principale\s+(.+?)\s+0[67](?:[ .-]?\d{2}){4}",
                r"Titulaire\s*:?\s*([^\d]+?)\s+(?:0\d|Forfait|Conso)",
            ],
        ),
        phone_number=_first_match(text, [r"(0[67](?:[ .-]?\d{2}){4})"]),
        data_used_gb=data_used_gb,
        data_limit_gb=data_limit_gb,
        national_data_used_gb=national_used_gb,
        national_data_limit_gb=national_limit_gb,
        roaming_data_used_gb=roaming_used_gb,
        roaming_data_limit_gb=roaming_limit_gb,
        out_of_plan_eur=out_of_plan,
        voice_used=_extract_section(text, "Appels"),
        sms_used=_extract_section(text, "SMS/MMS") or _extract_section(text, "SMS"),
        mms_used=_extract_section(text, "MMS"),
        next_reset_date=_extract_next_reset_date(text),
        raw_summary=text[:1000],
        fetched_at=datetime.now(timezone.utc),
    )


_FRENCH_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def _extract_next_reset_date(text: str) -> date | None:
    normalized = _strip_accents(text.lower())
    match = re.search(r"remis a zero le\s+(\d{1,2})\s+([a-z]+)\s+(\d{4})", normalized)
    if not match:
        return None
    month = _FRENCH_MONTHS.get(match.group(2))
    if month is None:
        return None
    return date(int(match.group(3)), month, int(match.group(1)))



def _extract_section_internet_pair_gb(payload: str, section_label: str) -> tuple[float | None, float | None]:
    section = _extract_raw_section(payload, section_label)
    if not section:
        return None, None

    match = re.search(
        r'customPercentageLabel"\s*:\s*"([^"]+)"[^{}]{0,500}label"\s*:\s*"Internet"',
        section,
        re.IGNORECASE,
    )
    if match:
        label = unescape(match.group(1)).strip()
    else:
        text = _normalize_payload_text(section)
        visible_match = re.search(
            r"Internet\s+(.+?)\s+(?:Appels|SMS/MMS|SMS|MMS|Hors-forfait|$)",
            text,
            re.IGNORECASE,
        )
        if not visible_match:
            return None, None
        label = visible_match.group(1).strip()

    if label.lower().startswith("illimit"):
        return None, None

    value_match = re.search(
        r'([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)\s*/\s*([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)',
        label,
        re.IGNORECASE,
    )
    if not value_match:
        return None, None
    return (
        _to_gb(value_match.group(1), value_match.group(2)),
        _to_gb(value_match.group(3), value_match.group(4)),
    )


def _extract_section_euros(payload: str, section_label: str) -> float | None:
    section = _extract_raw_section(payload, section_label)
    if not section:
        return None
    readable = unescape(section)
    match = re.search(
        r'Hors-forfait.*?children"\s*:\s*"([0-9]+(?:[,.][0-9]+)?)\s*€',
        readable,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        match = re.search(r'Hors-forfait.*?([0-9]+(?:[,.][0-9]+)?)\s*€', readable, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _extract_raw_section(payload: str, section_label: str) -> str | None:
    raw = unescape(payload)
    marker = f'children":"{section_label}"'
    start = raw.find(marker)
    if start == -1 and section_label == "À l'étranger":
        start = raw.find('children":"A l\'etranger"')
    if start == -1:
        start = raw.find(section_label)
    if start == -1:
        return None

    candidates = [
        pos
        for pos in [
            raw.find('children":"En France"', start + 1),
            raw.find('children":"À l\'étranger"', start + 1),
            raw.find("En France", start + 1),
            raw.find("À l'étranger", start + 1),
        ]
        if pos != -1
    ]
    end = min(candidates) if candidates else start + 6000
    return raw[start:end]

def _normalize_payload_text(payload: str) -> str:
    """Turn HTML or RSC-ish payload into searchable text."""
    unescaped = unescape(payload)
    if "<" in unescaped and ">" in unescaped:
        unescaped = BeautifulSoup(unescaped, "html.parser").get_text(" ", strip=True)
    unescaped = unescaped.replace('\\"', '"')
    unescaped = unescaped.replace("\\n", " ")
    unescaped = re.sub(r"[\[\]{}()$:;]", " ", unescaped)
    unescaped = re.sub(r"\s+", " ", unescaped)
    return unescaped.strip()


def _first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_data_pair_gb(text: str) -> tuple[float | None, float | None]:
    """Extract '<used> unit / <limit> unit' near Internet/Data labels."""
    patterns = [
        r"(?:Internet|Data|Données)[^0-9]{0,120}([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)\s*/\s*([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)",
        r"([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)\s*/\s*([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            used = _to_gb(match.group(1), match.group(2))
            limit = _to_gb(match.group(3), match.group(4))
            return used, limit
    single = _extract_data_gb(text)
    return single, None


def _extract_data_gb(text: str) -> float | None:
    patterns = [
        r"(?:Internet|Data|Données).*?([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)",
        r"([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio).*?(?:Internet|Data|Données)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _to_gb(match.group(1), match.group(2))
    return None


def _extract_roaming_data_gb(text: str) -> float | None:
    if not re.search(r"Roaming", text, re.IGNORECASE):
        return None
    match = re.search(r"Roaming[^0-9]{0,160}([0-9]+(?:[,.][0-9]+)?)\s*(Go|Gio|Mo|Mio)", text, re.IGNORECASE)
    if not match:
        return None
    return _to_gb(match.group(1), match.group(2))


def _extract_euros(text: str) -> float | None:
    match = re.search(r"Hors forfait[^0-9€]{0,120}([0-9]+(?:[,.][0-9]+)?)\s*€", text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _extract_section(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\s+([^€]+?)(?:Internet|Appels|SMS/MMS|SMS|MMS|Hors forfait|$)", text, re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).strip()
    return value[:120] if value else None


def _to_gb(value: str, unit: str) -> float:
    number = float(value.replace(",", "."))
    if unit.lower() in {"mo", "mio"}:
        return round(number / 1024, 6)
    return round(number, 3)
