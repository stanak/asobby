"""GeoIP country lookup for host addresses (MaxMind GeoLite2-Country)."""
from __future__ import annotations

import io
import ipaddress
import logging
import os
import socket
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

GEOLITE2_COUNTRY_URL = (
    "https://download.maxmind.com/geoip/databases/GeoLite2-Country/download?suffix=tar.gz"
)
DEFAULT_DB_PATH = Path(__file__).with_name("GeoLite2-Country.mmdb")

_reader = None
_cache: dict[str, Optional["CountryInfo"]] = {}


@dataclass(frozen=True)
class CountryInfo:
    code: str
    name: str


def db_path() -> Path:
    raw = os.environ.get("GEOIP_COUNTRY_DB", "").strip()
    return Path(raw) if raw else DEFAULT_DB_PATH


def host_ip_from_addr(addr: str) -> Optional[str]:
    try:
        host, port_s = addr.rsplit(":", 1)
        port = int(port_s)
        if not (0 < port < 65536):
            return None
        socket.inet_aton(host)
        if host.count(".") != 3:
            return None
        return host
    except (ValueError, OSError):
        return None


def lookup_country(ip: str) -> Optional[CountryInfo]:
    if ip in _cache:
        return _cache[ip]

    result: Optional[CountryInfo] = None
    if _reader is not None:
        try:
            addr = ipaddress.ip_address(ip)
            if isinstance(addr, ipaddress.IPv4Address) and not (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
            ):
                response = _reader.country(ip)
                code = response.country.iso_code
                if code:
                    name = (
                        response.country.names.get("ja")
                        or response.country.names.get("en")
                        or code
                    )
                    result = CountryInfo(code=code, name=name)
        except Exception:
            result = None

    _cache[ip] = result
    return result


def init_geoip(path: Optional[Path] = None) -> bool:
    global _reader
    target = path or db_path()
    if not target.is_file():
        log.warning("GeoIP database not found at %s", target)
        _reader = None
        return False

    import geoip2.database

    _reader = geoip2.database.Reader(str(target))
    log.info("GeoIP database loaded from %s", target)
    return True


def _download_geoip_db(path: Path, account_id: str, license_key: str) -> None:
    auth = (account_id, license_key)
    with httpx.Client(auth=auth, follow_redirects=True, timeout=120.0) as client:
        response = client.get(GEOLITE2_COUNTRY_URL)
        response.raise_for_status()

    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.name.endswith("GeoLite2-Country.mmdb"):
                extracted = archive.extractfile(member)
                if extracted is None:
                    break
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(extracted.read())
                log.info("GeoIP database downloaded to %s", path)
                return

    raise RuntimeError("GeoLite2-Country.mmdb not found in MaxMind archive")


def ensure_geoip_db(path: Optional[Path] = None) -> bool:
    target = path or db_path()
    if target.is_file():
        return True

    account_id = os.environ.get("GEOIP_MAXMIND_ACCOUNT_ID", "").strip()
    license_key = os.environ.get("GEOIP_MAXMIND_LICENSE_KEY", "").strip()
    if not account_id or not license_key:
        log.warning(
            "GeoIP database missing; set GEOIP_MAXMIND_ACCOUNT_ID and "
            "GEOIP_MAXMIND_LICENSE_KEY to download GeoLite2-Country"
        )
        return False

    try:
        _download_geoip_db(target, account_id, license_key)
    except Exception:
        log.exception("Failed to download GeoIP database")
        return False
    return target.is_file()


def apply_country_from_addr(
    post: object,
    *,
    addr: str,
    country_code_attr: str = "country_code",
    country_name_attr: str = "country_name",
) -> None:
    host = host_ip_from_addr(addr)
    if not host:
        setattr(post, country_code_attr, "")
        setattr(post, country_name_attr, "")
        return

    info = lookup_country(host)
    if info is None:
        setattr(post, country_code_attr, "")
        setattr(post, country_name_attr, "")
        return

    setattr(post, country_code_attr, info.code)
    setattr(post, country_name_attr, info.name)
