"""GeoIP lookup tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import geoip


def test_host_ip_from_addr_valid():
    assert geoip.host_ip_from_addr("1.2.3.4:10800") == "1.2.3.4"


def test_host_ip_from_addr_invalid():
    assert geoip.host_ip_from_addr("not-an-addr") is None
    assert geoip.host_ip_from_addr("999.999.999.999:10800") is None


def test_lookup_country_private_ip():
    geoip._cache.clear()
    geoip._reader = MagicMock()
    assert geoip.lookup_country("192.168.0.1") is None
    geoip._reader.country.assert_not_called()


def test_lookup_country_public_ip():
    geoip._cache.clear()
    reader = MagicMock()
    response = MagicMock()
    response.country.iso_code = "JP"
    response.country.names = {"ja": "日本", "en": "Japan"}
    reader.country.return_value = response
    geoip._reader = reader

    info = geoip.lookup_country("1.2.3.4")
    assert info is not None
    assert info.code == "JP"
    assert info.name == "日本"
    assert geoip.lookup_country("1.2.3.4") == info


def test_apply_country_from_addr():
    class Post:
        country_code = ""
        country_name = ""

    post = Post()
    with patch.object(
        geoip,
        "lookup_country",
        return_value=geoip.CountryInfo(code="US", name="アメリカ"),
    ):
        geoip.apply_country_from_addr(post, addr="8.8.8.8:10800")

    assert post.country_code == "US"
    assert post.country_name == "アメリカ"
