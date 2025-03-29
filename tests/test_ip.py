import pytest
import requests
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from src.ip import app, lookup_geo_info, is_cmd


client = TestClient(app)

# Mock data for testing
MOCK_GEO_INFO = {
    "country": "Vietnam",
    "regionName": "Northern",
    "city": "Hanoi",
    "isp": "Viettel",
    "proxy": False
}

@pytest.fixture
def mock_request():
    request = MagicMock()
    request.headers = {
        "user-agent": "Mozilla/5.0",
        "x-forwarded-for": "1.2.3.4"
    }
    request.client.host = "1.2.3.4"
    return request

def test_lookup_geo_info_success():
    with patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = MOCK_GEO_INFO
        mock_get.return_value.raise_for_status.return_value = None
        
        result = lookup_geo_info("1.2.3.4")
        assert result == MOCK_GEO_INFO

def test_lookup_geo_info_failure():
    with patch('requests.get') as mock_get:
        mock_get.side_effect = requests.RequestException("API Error")
        
        with pytest.raises(Exception) as exc_info:
            lookup_geo_info("1.2.3.4")
        assert "Failed to fetch geolocation data" in str(exc_info.value)

def test_is_cmd_true():
    result = {"user_agent": "curl/7.1.1"}
    assert is_cmd(result) is True

def test_is_cmd_false():
    result = {"user_agent": "Mozilla/5.0"}
    assert is_cmd(result) is False

def test_is_cmd_no_user_agent():
    result = {}
    assert is_cmd(result) is False

def test_root_endpoint_html():
    response = client.get("/", headers={"user-agent": "Mozilla/5.0"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_root_endpoint_cli():
    response = client.get("/", headers={"user-agent": "curl/7.1.1"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"

def test_json_endpoint():
    with patch('src.ip.lookup_geo_info') as mock_lookup:
        mock_lookup.return_value = MOCK_GEO_INFO
        response = client.get("/json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        data = response.json()
        assert "ip" in data
        assert "city" in data
        assert "country" in data

# def test_404_handler():
#     response = client.get("/nonendpoint")
#     assert response.status_code == 404 