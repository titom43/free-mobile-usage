# Free Mobile Usage for Home Assistant

Custom Home Assistant integration to monitor Free Mobile plan usage for family lines.

## Status

Beta integration. It uses the structured endpoints observed in the Free iOS
application to retrieve all lines attached to a primary account. Free Mobile
does not publish this API as a supported public API, so endpoint changes remain
possible.

## Features planned

- One Home Assistant config entry for the primary Free Mobile account.
- A separate Home Assistant device and sensor set for every family line.
- First-class roaming sensors: roaming data used, limit, remaining, percent used, and out-of-plan amount.
- National data sensors where the portal exposes a finite allowance.
- Attributes for phone number, account name, voice/SMS/MMS summaries where available.
- Home Assistant automations can alert on data thresholds and out-of-plan costs.
- Telegram summary command can be added later via Home Assistant automation.

## Installation with HACS

1. HACS > Integrations > Custom repositories.
2. Add this repository URL as category `Integration`.
3. Download `Free Mobile Usage`.
4. Restart Home Assistant.
5. Settings > Devices & services > Add integration > Free Mobile Usage.
6. Add `Free Mobile Usage`, then enter the primary account credentials.
7. If Free Mobile requests it, enter the SMS code in the second step. The Home
   Assistant instance is then registered as a trusted device.

The integration retains its access token and trusted-device identifier in the
Home Assistant config entry, alongside the password. It tries that token before
using the password and only asks for a new SMS verification when Free rejects
the trusted device.

## Local test before installing in Home Assistant

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install aiohttp beautifulsoup4
FREEMOBILE_USERNAME=12345678 FREEMOBILE_PASSWORD='secret' python scripts/test_login.py
```

The script tries saved API access tokens before attempting a login. If the token
has expired, it retries `login + trustedUuid`; Free should then accept the
trusted device without a new SMS challenge. Only if Free explicitly returns a
2FA challenge does the script prompt locally for the temporary SMS code with
hidden input. Do not paste SMS codes in chat, commits, or shell history.

The local test stores reusable authentication state in
`private/free_mobile_mobile_tokens.json` and its session cookies in
`private/free_mobile_mobile_cookies.jar`, both ignored by Git and created with
private filesystem permissions. Delete both files only if you want to force a
fresh login and SMS challenge.

All family lines returned by the primary account are fetched automatically. To
test one line only, add `FREEMOBILE_LINE_ID=<line-id>`.

Do not commit `.env`, captured private HTML, or credentials.

## Sensors

For each family line, the integration creates:

- Data used
- Data remaining
- Data used percent
- National data used
- National data limit
- Roaming data used
- Roaming data limit
- Roaming data remaining
- Roaming data used percent
- Out of plan when Free returns an unambiguous value. A non-zero API billing
  counter is not currently exposed because its monetary unit is undocumented.
- Next reset date
- Last update

## Telegram summary idea

A later Home Assistant automation can listen to a Telegram command such as `/conso` and reply with all `free_mobile_usage` sensor values.

## Roaming alerts

The intended Home Assistant alert sensors are:

- `*_roaming_data_used`
- `*_roaming_data_limit`
- `*_roaming_data_remaining`
- `*_roaming_data_used_percent`
- `*_out_of_plan`

These should drive Telegram alerts before national data sensors.
