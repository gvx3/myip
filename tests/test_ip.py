from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

import src.ip as ip_module
from src.ip import (
    app,
    get_valid_ip_from_header,
    is_cmd,
    is_public_ip,
    lookup_geo_info,
    lookup_ip,
)

client = TestClient(app)

# Mock data for testing
MOCK_GEO_INFO = {
    "country": "Vietnam",
    "countryCode": "VN",
    "regionName": "Northern",
    "city": "Hanoi",
    "zip": "100000",
    "lat": 21.0285,
    "lon": 105.8542,
    "timezone": "Asia/Bangkok",
    "isp": "Viettel",
    "org": "Viettel Group",
    "as": "AS7552 Viettel Group",
    "proxy": False,
    "hosting": False,
    "mobile": True,
}


@pytest.fixture(autouse=True)
def clear_caches():
    """Keep the module-level geo cache and rate-limit windows isolated per test."""
    ip_module._geo_cache.clear()
    ip_module._rate_windows.clear()
    yield


@pytest.fixture
def mock_request():
    request = MagicMock()
    request.headers = {
        "user-agent": "Mozilla/5.0",
        "x-forwarded-for": "1.2.3.4",
    }
    request.client.host = "1.2.3.4"
    return request


def _mock_geo_response(mock_get):
    mock_get.return_value.json.return_value = MOCK_GEO_INFO
    mock_get.return_value.raise_for_status.return_value = None


# --- lookup_geo_info ---

def test_lookup_geo_info_success():
    with patch("requests.get") as mock_get:
        _mock_geo_response(mock_get)

        result = lookup_geo_info("1.2.3.4")
        assert result == MOCK_GEO_INFO


def test_lookup_geo_info_failure_returns_empty_dict():
    with patch("src.ip.logger") as mock_logger, patch("requests.get") as mock_get:
        mock_get.side_effect = requests.RequestException("API Error")
        result = lookup_geo_info("1.2.3.4")

    assert result == {}
    mock_logger.error.assert_called_once()
    message = mock_logger.error.call_args[0][0]
    assert "Failed to fetch geolocation data" in message


def test_lookup_geo_info_skips_non_public_ip():
    with patch("requests.get") as mock_get:
        result = lookup_geo_info("127.0.0.1")

    assert result == {}
    mock_get.assert_not_called()


def test_lookup_geo_info_caches_success():
    with patch("requests.get") as mock_get:
        _mock_geo_response(mock_get)

        first = lookup_geo_info("1.2.3.4")
        second = lookup_geo_info("1.2.3.4")

    assert first == MOCK_GEO_INFO == second
    assert mock_get.call_count == 1


def test_lookup_geo_info_cache_expires(monkeypatch):
    monkeypatch.setattr(ip_module, "GEO_CACHE_TTL", 0)
    with patch("requests.get") as mock_get:
        _mock_geo_response(mock_get)

        lookup_geo_info("1.2.3.4")
        lookup_geo_info("1.2.3.4")

    assert mock_get.call_count == 2


# --- is_public_ip ---

@pytest.mark.parametrize(
    "ip,expected",
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("192.168.1.10", False),
        ("fe80::1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(ip, expected):
    assert is_public_ip(ip) is expected


# --- get_valid_ip_from_header ---

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("8.8.8.8", "8.8.8.8"),
        (" 1.1.1.1 ", "1.1.1.1"),
        ("2606:4700:4700::1111", "2606:4700:4700::1111"),
        ("::ffff:1.2.3.4", "1.2.3.4"),
        ("127.0.0.1", None),
        ("10.0.0.5", None),
        ("fe80::1", None),
        ("not-an-ip", None),
    ],
)
def test_get_valid_ip_from_header(raw, expected):
    assert get_valid_ip_from_header(raw) == expected


# --- lookup_ip ---

def test_lookup_ip_from_forwarded_header(mock_request):
    assert lookup_ip(mock_request) == "1.2.3.4"


def test_lookup_ip_uses_cf_connecting_ip_first():
    request = MagicMock()
    request.headers = {
        "cf-connecting-ip": "1.1.1.1",
        "x-forwarded-for": "8.8.8.8",
    }
    request.client.host = "8.8.8.8"
    assert lookup_ip(request) == "1.1.1.1"


def test_lookup_ip_skips_private_forwarded_entries():
    request = MagicMock()
    request.headers = {"x-forwarded-for": "10.0.0.1, 8.8.8.8"}
    request.client.host = "127.0.0.1"
    assert lookup_ip(request) == "8.8.8.8"


def test_lookup_ip_falls_back_to_public_client_host():
    request = MagicMock()
    request.headers = {}
    request.client.host = "8.8.8.8"
    assert lookup_ip(request) == "8.8.8.8"


def test_lookup_ip_returns_private_client_host_as_last_resort():
    request = MagicMock()
    request.headers = {}
    request.client.host = "127.0.0.1"
    assert lookup_ip(request) == "127.0.0.1"


def test_lookup_ip_ignores_headers_when_proxy_untrusted(monkeypatch):
    monkeypatch.setattr(ip_module, "TRUST_PROXY", False)
    request = MagicMock()
    request.headers = {"x-forwarded-for": "8.8.8.8", "cf-connecting-ip": "1.1.1.1"}
    request.client.host = "1.2.3.4"
    assert lookup_ip(request) == "1.2.3.4"


# --- is_cmd ---

def test_is_cmd_true():
    assert is_cmd("curl/7.1.1") is True


def test_is_cmd_false():
    assert is_cmd("Mozilla/5.0") is False


def test_is_cmd_no_user_agent():
    assert is_cmd("") is False


# --- endpoints ---

def test_root_endpoint_html():
    response = client.get("/", headers={"user-agent": "Mozilla/5.0"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_root_endpoint_cli():
    response = client.get("/", headers={"user-agent": "curl/7.1.1"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_root_endpoint_cli_skips_geolocation():
    with patch("src.ip.lookup_geo_info") as mock_geo:
        response = client.get("/", headers={"user-agent": "curl/7.1.1"})
    assert response.status_code == 200
    mock_geo.assert_not_called()


def test_ip_endpoint_returns_bare_ip():
    response = client.get("/ip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_healthz_endpoint():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_json_endpoint():
    with patch("src.ip.lookup_geo_info") as mock_lookup:
        mock_lookup.return_value = MOCK_GEO_INFO
        response = client.get("/json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        data = response.json()
        assert "ip" in data
        assert "city" in data
        assert "country" in data


def test_json_endpoint_returns_richer_fields():
    with patch("src.ip.lookup_geo_info", return_value=MOCK_GEO_INFO):
        data = client.get("/json").json()

    assert data["country_code"] == "VN"
    assert data["zip"] == "100000"
    assert data["timezone"] == "Asia/Bangkok"
    assert data["org"] == "Viettel Group"
    assert data["asn"] == "AS7552 Viettel Group"
    assert data["mobile"] == "Yes"
    assert data["hosting"] == "No"
    assert data["map_url"].startswith("https://www.openstreetmap.org/")


def test_lookup_flag_uses_requested_public_ip():
    with patch("src.ip.lookup_geo_info", return_value=MOCK_GEO_INFO) as mock_geo:
        response = client.get("/json?ip=8.8.8.8")

    assert response.status_code == 200
    assert response.json()["ip"] == "8.8.8.8"
    mock_geo.assert_called_once_with("8.8.8.8")


def test_lookup_flag_rejects_non_public_ip():
    with patch("src.ip.lookup_geo_info") as mock_geo:
        response = client.get("/json?ip=10.0.0.1")

    assert response.status_code == 400
    mock_geo.assert_not_called()


def test_lookup_flag_is_rate_limited(monkeypatch):
    monkeypatch.setattr(ip_module, "LOOKUP_RATE_LIMIT", 1)
    with patch("src.ip.lookup_geo_info", return_value=MOCK_GEO_INFO):
        first = client.get("/json?ip=8.8.8.8")
        second = client.get("/json?ip=8.8.8.8")

    assert first.status_code == 200
    assert second.status_code == 429


def test_404_handler():
    response = client.get("/nonexistent")
    assert response.status_code == 404
    assert "404" in response.text
