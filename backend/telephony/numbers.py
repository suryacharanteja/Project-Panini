"""Conservative E.164 destination rules and local calling windows from libphonenumber metadata."""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import phonenumbers as pn
from phonenumbers import shortnumberinfo, timezone


def validate(number):
    if not isinstance(number, str) or not re.fullmatch(r"\+[1-9]\d{7,14}", number):
        raise ValueError("Use a full E.164 number. Emergency numbers and short codes cannot be called.")
    parsed = pn.parse(number, None)
    if pn.format_number(parsed, pn.PhoneNumberFormat.E164) != number:
        raise ValueError("Use the canonical E.164 number, without a national trunk prefix.")
    region = pn.region_code_for_number(parsed)
    if (
        not pn.is_valid_number(parsed)
        or shortnumberinfo.is_valid_short_number(parsed)
        or pn.number_type(parsed)
        not in {
            pn.PhoneNumberType.FIXED_LINE,
            pn.PhoneNumberType.MOBILE,
            pn.PhoneNumberType.FIXED_LINE_OR_MOBILE,
        }
        or not region
        or region == "001"
    ):
        raise ValueError(
            "Only valid geographic fixed-line or mobile numbers are supported; service and premium numbers are blocked."
        )
    zones = timezone.time_zones_for_number(parsed)
    if not zones or "Etc/Unknown" in zones:
        raise ValueError("The destination's calling timezone cannot be determined.")
    return {
        "number": pn.format_number(parsed, pn.PhoneNumberFormat.E164),
        "region": region,
        "timezones": list(zones),
    }


def calling_window(number, now):
    details = validate(number)
    instant = datetime.fromtimestamp(now, UTC)

    def allowed(at):
        return all(9 <= at.astimezone(ZoneInfo(zone)).hour < 20 for zone in details["timezones"])

    if allowed(instant):
        return {**details, "allowed": True, "next_at": now}
    # When metadata has multiple possible zones, every zone must be within the window.
    for minutes in range(1, 3 * 24 * 60):
        candidate = instant.replace(second=0, microsecond=0) + timedelta(minutes=minutes)
        if allowed(candidate):
            return {**details, "allowed": False, "next_at": candidate.timestamp()}
    return {**details, "allowed": False, "next_at": None}
