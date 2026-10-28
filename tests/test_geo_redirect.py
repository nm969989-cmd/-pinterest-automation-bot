import pytest
from flask import Flask
from keep_alive import app, get_visitor_country
from database import create_tracked_link, record_link_click, get_click_stats

def test_get_visitor_country():
    with app.test_request_context("/", headers={"CF-IPCountry": "IN"}):
        from flask import request
        assert get_visitor_country(request) == "IN"

    with app.test_request_context("/", headers={"CF-IPCountry": "us"}):
        from flask import request
        assert get_visitor_country(request) == "US"

    with app.test_request_context("/", headers={"X-Country-Code": "BR"}):
        from flask import request
        assert get_visitor_country(request) == "BR"

    with app.test_request_context("/"):
        from flask import request
        assert get_visitor_country(request) == ""


def test_geo_redirect_routing():
    client = app.test_client()
    # Use a live non-dead ASIN so stock healer doesn't trigger mock replacement
    code = create_tracked_link(
        target_url="https://www.amazon.com/dp/B0CHR8R1L3?tag=animeasthet-20",
        anime_name="Attack on Titan",
        title="Levi Ackerman Action Figure"
    )

    # 1. India visitor (CF-IPCountry: IN) should be redirected to amazon.in with animeasthet06-21
    resp_in = client.get(f"/r/{code}", headers={"CF-IPCountry": "IN"})
    assert resp_in.status_code == 302
    loc_in = resp_in.headers.get("Location", "")
    assert "amazon.in" in loc_in
    assert "animeasthet06-21" in loc_in

    # 2. US visitor (CF-IPCountry: US) should be redirected to amazon.com with animeasthet-20
    resp_us = client.get(f"/r/{code}", headers={"CF-IPCountry": "US"})
    assert resp_us.status_code == 302
    loc_us = resp_us.headers.get("Location", "")
    assert "amazon.com" in loc_us
    assert "animeasthet-20" in loc_us


def test_bridge_page_localization():
    client = app.test_client()
    code = create_tracked_link(
        target_url="https://www.amazon.com/dp/B0CHR8R1L3?tag=animeasthet-20",
        anime_name="Demon Slayer",
        title="Tanjiro Figure"
    )

    # India visitor sees Indian button
    resp_in = client.get(f"/p/{code}", headers={"CF-IPCountry": "IN"})
    assert resp_in.status_code == 200
    assert "Shop on Amazon India (₹)" in resp_in.text
    assert "m=amazon_in" in resp_in.text

    # US visitor sees Global button
    resp_us = client.get(f"/p/{code}", headers={"CF-IPCountry": "US"})
    assert resp_us.status_code == 200
    assert "Shop on Amazon ($)" in resp_us.text
    assert "m=amazon_us" in resp_us.text


def test_country_analytics_logging():
    code = create_tracked_link("https://www.amazon.com/dp/B0CHR8R1L3", "Naruto", "Naruto Poster")
    record_link_click(code, "Test-Agent", "https://pinterest.com/", merchant="amazon_in", country="IN")
    record_link_click(code, "Test-Agent", "https://pinterest.com/", merchant="amazon_us", country="US")

    stats = get_click_stats()
    assert "top_countries" in stats
    countries_found = [c[0] for c in stats["top_countries"]]
    assert "IN" in countries_found or "US" in countries_found
