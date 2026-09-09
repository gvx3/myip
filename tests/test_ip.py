from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

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
    "regionName": "Northern",
    "city": "Hanoi",
    "isp": "Viettel",
    "proxy": False,
}


@pytest.fixture
def mock_request():
    request = MagicMock()
    request.headers = {
        "user-agent": "Mozilla/5.0",
        "x-forwarded-for": "1.2.3.4",
    }
    request.client.host = "1.2.3.4"
    return request


# --- lookup_geo_info ---

def test_lookup_geo_info_success():
    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = MOCK_GEO_INFO
        mock_get.return_value.raise_for_status.return_value = None

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


# --- is_cmd ---

def test_is_cmd_true():
    result = {"user_agent": "curl/7.1.1"}
    assert is_cmd(result) is True


def test_is_cmd_false():
    result = {"user_agent": "Mozilla/5.0"}
    assert is_cmd(result) is False


def test_is_cmd_no_user_agent():
    result = {}
    assert is_cmd(result) is False


# --- endpoints ---

def test_root_endpoint_html():
    response = client.get("/", headers={"user-agent": "Mozilla/5.0"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_root_endpoint_cli():
    response = client.get("/", headers={"user-agent": "curl/7.1.1"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


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


def test_404_handler():
    response = client.get("/nonexistent")
    assert response.status_code == 404
    assert "404" in response.text
