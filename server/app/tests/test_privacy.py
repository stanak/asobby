"""The policy/contact must be readable without authentication, storage or scripts."""
from html.parser import HTMLParser

import httpx
import pytest

import main


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.asyncio
@pytest.mark.parametrize("query,lang", [
    ("", "ja"), ("?lang=ja", "ja"), ("?lang=en", "en"),
    ("?lang=unknown", "ja"), ("?lang=../../README.md", "ja"),
    ("?lang=%3Cscript%3Ealert(1)%3C/script%3E", "ja"),
])
async def test_public_policy_needs_no_session_or_database(monkeypatch, query, lang):
    def unavailable(*args, **kwargs):
        raise AssertionError("The policy must not touch authentication or the DB")

    monkeypatch.setattr(main, "resolve_session", unavailable)
    monkeypatch.setattr(main.db, "session", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="https://asobby.test",
        cookies={"asobby_session": "expired-invalid-session"},
    ) as client:
        response = await client.get("/privacy" + query)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in response.headers
    filename = "privacy-en.html" if lang == "en" else "privacy.html"
    assert response.text == (main.STATIC_DIR / filename).read_text(encoding="utf-8")
    assert f'<html lang="{lang}">' in response.text


@pytest.mark.parametrize("filename", ["privacy.html", "privacy-en.html"])
def test_policy_is_static_self_contained_and_has_accessible_contact(filename):
    source = (main.STATIC_DIR / filename).read_text(encoding="utf-8")
    doc = Document(source)
    ids = [attrs["id"] for _, attrs in doc.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert set(ids) == {
        "policy", "collection", "sharing", "statistics", "replays", "services",
        "retention", "security", "contact", "changes",
    }
    assert sum(tag == "h1" for tag, _ in doc.elements) == 1
    hrefs = [attrs["href"] for tag, attrs in doc.elements if tag == "a"]
    assert "mailto:midoristar@empengineer.cool" in hrefs
    assert "https://discord.com/users/204966469593858048" in hrefs
    for tag, attrs in doc.elements:
        assert tag not in {"script", "iframe", "form", "object", "embed"}
        assert not any(name.startswith("on") for name in attrs)
        if "src" in attrs:
            assert attrs["src"].startswith("/static/")
            assert (main.STATIC_DIR / attrs["src"].removeprefix("/static/")).is_file()
        if tag == "a" and attrs.get("href", "").startswith("#"):
            assert attrs["href"][1:] in ids
    assert "dpalette" in source
    assert "Google Analytics" in source
    assert "2026-09-20" in source


@pytest.mark.parametrize("page", ["index", "guide", "settings", "stats", "replays", "feedback", "players"])
def test_public_page_navigation_links_to_policy(page):
    source = (main.STATIC_DIR / f"{page}.html").read_text(encoding="utf-8")
    doc = Document(source)
    assert any(tag == "a" and attrs.get("href") == "/privacy"
               and attrs.get("data-i18n") == "nav.privacy" for tag, attrs in doc.elements)
    assert "/static/i18n.js?v=privacy-1" in source
