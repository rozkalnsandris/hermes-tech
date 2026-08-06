#!/usr/bin/env python3
"""Explicit UTC and Europe/Berlin time contracts for Hermes Tech."""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BUSINESS_TIMEZONE_NAME = "Europe/Berlin"
PUBLICATION_LOCAL_TIME = time(hour=7, minute=0, second=0)

try:
    BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)
except ZoneInfoNotFoundError as exc:  # pragma: no cover - host packaging failure
    raise RuntimeError(
        f"trūkst IANA laika zonas datu: {BUSINESS_TIMEZONE_NAME}"
    ) from exc

_REAL_DATETIME = datetime
_UTC_NOW_PROVIDER: Callable[[], datetime] = lambda: _REAL_DATETIME.now(timezone.utc)
_DIGEST_INSTALL_SENTINEL = "_HERMES_TIME_CONTRACTS_V1"
_COLLECTOR_INSTALL_SENTINEL = "_HERMES_RSS_TIME_CONTRACTS_V1"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("instantam jābūt timezone-aware")
    return value


def utc_now() -> datetime:
    """Return the current instant as an aware UTC datetime."""
    return _require_aware(_UTC_NOW_PROVIDER()).astimezone(timezone.utc)


def business_datetime(instant: datetime | None = None) -> datetime:
    """Convert an instant to the Hermes business timezone."""
    source = utc_now() if instant is None else _require_aware(instant)
    return source.astimezone(BUSINESS_TIMEZONE)


def business_date(instant: datetime | None = None) -> str:
    """Return the Europe/Berlin calendar date for an instant."""
    return business_datetime(instant).date().isoformat()


def parse_rfc3339_instant(raw: str) -> datetime:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = _REAL_DATETIME.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"nederīgs RFC3339 instant: {raw!r}") from exc
    return _require_aware(parsed)


def publication_timestamp(date_text: str) -> str:
    """Return 07:00 Europe/Berlin as RFC3339 for a business date."""
    try:
        parsed_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise ValueError(f"nederīgs datums: {date_text!r}") from exc
    if parsed_date.isoformat() != date_text:
        raise ValueError(f"datumam jābūt YYYY-MM-DD formātā: {date_text!r}")
    local = _REAL_DATETIME.combine(
        parsed_date,
        PUBLICATION_LOCAL_TIME,
        tzinfo=BUSINESS_TIMEZONE,
    )
    return local.isoformat(timespec="seconds")


def rss_struct_time_to_utc_iso(value: Any) -> str:
    """Convert a feedparser UTC struct_time without consulting host TZ."""
    if value is None:
        return ""
    try:
        epoch_seconds = calendar.timegm(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"nederīgs RSS UTC struct_time: {value!r}") from exc
    return _REAL_DATETIME.fromtimestamp(
        epoch_seconds,
        tz=timezone.utc,
    ).isoformat()


def collector_entry_published(entry: Any) -> str:
    """Return published/updated feedparser timestamps as canonical UTC."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return rss_struct_time_to_utc_iso(parsed)
    return ""


class DigestDateTime(_REAL_DATETIME):
    """Compatibility datetime that gives UTC `now()` Berlin date semantics.

    Existing audit timestamps keep their real UTC instant and offset. Only the
    exact business-date format `%Y-%m-%d` is projected into Europe/Berlin.
    """

    @classmethod
    def now(cls, tz: Any = None) -> "DigestDateTime":
        if tz is None:
            current = _REAL_DATETIME.now()
            return cls(
                current.year,
                current.month,
                current.day,
                current.hour,
                current.minute,
                current.second,
                current.microsecond,
                fold=current.fold,
            )
        current = utc_now().astimezone(tz)
        return cls.fromtimestamp(current.timestamp(), tz=tz)

    def strftime(self, format: str) -> str:
        if (
            format == "%Y-%m-%d"
            and self.tzinfo is not None
            and self.utcoffset() is not None
        ):
            return self.astimezone(BUSINESS_TIMEZONE).date().isoformat()
        return super().strftime(format)


def install_collector_time_contracts(core: Any) -> None:
    """Install host-TZ-independent RSS timestamp conversion into collector."""
    if getattr(core, _COLLECTOR_INSTALL_SENTINEL, False):
        return
    if not hasattr(core, "entry_published"):
        return
    core.entry_published = collector_entry_published
    setattr(core, _COLLECTOR_INSTALL_SENTINEL, True)


def install_digest_time_contracts(core: Any) -> None:
    """Install Europe/Berlin business-date semantics into the legacy core."""
    if getattr(core, _DIGEST_INSTALL_SENTINEL, False):
        return
    if not hasattr(core, "datetime"):
        return
    core.datetime = DigestDateTime
    setattr(core, _DIGEST_INSTALL_SENTINEL, True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    business = sub.add_parser("business-date")
    business.add_argument("--at", help="aware RFC3339 instant; defaults to now")
    publication = sub.add_parser("publication-timestamp")
    publication.add_argument("date")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "business-date":
            instant = parse_rfc3339_instant(args.at) if args.at else None
            print(business_date(instant))
            return 0
        if args.command == "publication-timestamp":
            print(publication_timestamp(args.date))
            return 0
    except ValueError as exc:
        print(f"KĻŪDA: {exc}", file=__import__("sys").stderr)
        return 2
    raise AssertionError(f"neapstrādāta komanda: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
