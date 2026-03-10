import os
import pytest
from dotenv import load_dotenv
from fastapi import HTTPException
from fastapi.testclient import TestClient
import requests
import tempfile
import csv
from unittest.mock import MagicMock, patch, AsyncMock, patch
import json
from app.main import app, health_check_loop, CHECK_INTERVAL
from app.utils import build_request_headers, validate_and_probe_subnet
import asyncio


load_dotenv() # loads those secretz

client = TestClient(app)

def test_index():
    response = client.get("/")
    assert response.status_code == 200
    
    data = response.json()

    # --- root json keys ---
    assert "app" in data
    assert "server" in data
    assert "system" in data

    # --- app json keys ---
    app_info = data["app"]
    assert app_info["name"] == "HomeLab Probe API"
    assert app_info["version"] == "1.0.0"

    # --- server json keys ---
    server = data["server"]
    assert "python_version" in server
    assert "fastapi_version" in server
    assert "uvicorn_version" in server
    assert "hostname" in server
    assert "uptime_seconds" in server
    assert "current_time_utc" in server
    assert "epoch_time" in server


    # uptime should be non-negative
    assert server["uptime_seconds"] >= 0

    # --- System section ---
    system = data["system"]
    assert "cpu_count" in system
    assert "cpu_load" in system
    assert "memory" in system
    assert "disk_usage" in system

    # cpu_count should be an int
    assert isinstance(system["cpu_count"], int)

    # cpu_load should be a tuple/list of 3 values
    assert isinstance(system["cpu_load"], (list, tuple))
    assert len(system["cpu_load"]) == 3

    # memory and disk usage should be dicts
    assert isinstance(system["memory"], dict)
    assert isinstance(system["disk_usage"], dict)



@pytest.mark.asyncio
async def test_health_check_loop_runs_once_and_sleeps():
    # Mock run_async_health_check so it doesn't actually run
    with patch("app.main.run_async_health_check", new=AsyncMock()) as mock_check, \
         patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)) as mock_sleep:

        # Run the loop, expecting it to cancel after first sleep
        with pytest.raises(asyncio.CancelledError):
            await health_check_loop()

        # Ensure health check was called once
        mock_check.assert_awaited_once()

        # Ensure sleep was called with CHECK_INTERVAL
        mock_sleep.assert_awaited_once_with(CHECK_INTERVAL)


def test_probe_url_required_query_parameters():
    response = client.get("/probe/url")
    data = response.json()
    assert response.status_code == 422
    assert isinstance(data["detail"], list) # if request does not contain required parameters 
    assert len(data["detail"])== 2 #currently there are two required query parameters



@pytest.mark.asyncio
@patch("app.main.asyncio.sleep", new_callable=AsyncMock)
@patch("app.main.httpx.AsyncClient.get", new_callable=AsyncMock)
async def test_probe_url_request_count(mock_get, mock_sleep):
    # Mock the async GET response
    mock_response = AsyncMock()
    mock_response.is_success = False
    mock_response.status_code = 500
    mock_response.content = b"error"
    mock_get.return_value = mock_response

    # First request: count=2
    response = client.get("/probe/url", params={
        "count": 2,
        "url": f"http://{os.getenv('APP_DNS')}:8000",
        "delay": 2,
        "back_off": 3,
        "ssl": True
    })

    assert response.status_code == 200
    assert mock_sleep.await_count == 2

    # Second request: count=10
    response = client.get("/probe/url", params={
        "count": 10,
        "url": f"http://{os.getenv('APP_DNS')}:{os.getenv('PORT')}",
        "delay": 2,
        "back_off": 3,
    })

    assert response.status_code == 200
    # Total sleeps = 2 (first call) + 10 (second call)
    assert mock_sleep.await_count == 12


def test_probe_url_missing_protocol_error():
    response = client.get("/probe/url", params={
        "count": 2,
        "url": f"{os.getenv('APP_DNS')}:{os.getenv('PORT')}",
        "delay": 2, 
        "back_off": 3,
        "ssl": True
    })
    assert response.json() == {'detail': f"Error please provide the full URL of the web app to test. i.e. http://{os.getenv('APP_DNS')}:{os.getenv('PORT')}"}

#Patching subprocess otherwise test case will actually ping these! Which is cool but takes some time :)
@patch("app.utils.subprocess")
def test_probe_subnet_logic(mock_subprocess):
    #testing Valid subnet
    response = client.get("/probe/subnet", params={
        "subnet": "192.168.1.0/24"
    })
    data = response.json()
    assert "subnet" in data
    assert "total_hosts" in data
    assert "alive_hosts" in data
    #testing Invalid class C 
    response = client.get("/probe/subnet", params={
        "subnet": "168.100.1.0/16"
    })
    assert response.status_code == 400

    response = client.get("/probe/subnet", params={
        "subnet": "192.168.1.0/50"
    })
    assert response.status_code == 400

def test_invalid_subnet_format():
    with pytest.raises(HTTPException) as exc:
        validate_and_probe_subnet("not-a-subnet")
    assert exc.value.status_code == 400
    assert "Invalid subnet format" in exc.value.detail



invalid_cases = [
    {"subnet": "10.0.0.0/24", "msg": "Class C range"},
    {"subnet": "172.16.0.0/24", "msg": "Class C range"},
    {"subnet": "192.168.1.0", "msg": "Invalid subnet format"},
    {"subnet": "192.168.0.0/16", "msg": "Subnet must be /24 or smaller"},
    {"subnet": "300.300.300.0/24", "msg": "Invalid subnet format"},
]


@pytest.mark.parametrize(
    "subnet,expected_msg",
    [(case["subnet"], case["msg"]) for case in invalid_cases]
)
def test_subnet_errors(subnet, expected_msg):
    with pytest.raises(HTTPException) as exc:
        validate_and_probe_subnet(subnet)

    assert exc.value.status_code == 400
    assert expected_msg in exc.value.detail


def test_build_request_headers():
    header_list = []
    headers = build_request_headers(header_list)
    assert headers == {}

    header_list = ["X-API-KEY: UDM_SE_API_KEY"]
    headers = build_request_headers(header_list)
    assert headers != {}

    #test case for checking if env variable injection is working.
    header_list = ["X-API-KEY: UDM_SE_API_KEY"]
    headers = build_request_headers(header_list)
    assert headers["X-API-KEY"] == os.getenv("UDM_SE_API_KEY")

def test_delete_service_success(tmp_path):
    # Create fake config file
    config_path = tmp_path / "services.json"
    config_path.write_text(json.dumps({
        "services": {
            "vaultwarden": {"status": 200},
            "traefik": {"status": 401}
        }
    }))

    # Patch CONFIG_PATH to point to our temp file
    with patch("os.getenv", return_value=str(config_path)):
        #traefik is the service name provided it will be deleted.
        response = client.delete("/probe/services/traefik")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "service": "traefik"}

    # Verify file was updated
    updated = json.loads(config_path.read_text())
    assert "traefik" not in updated["services"]
    assert "vaultwarden" in updated["services"]

def test_get_logs_returns_parsed_csv():
    # Create a temporary CSV file
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
        writer = csv.writer(tmp)
        writer.writerow(["timestamp", "service", "status", "content_length"])
        writer.writerow(["2024-01-01T00:00:00Z", "auth", "200", "123"])
        writer.writerow(["2024-01-01T00:00:00Z", "db", "500", "0"])
        tmp_path = tmp.name

    # Patch LOG_PATH to point to our temp file
    with patch.dict(os.environ, {"LOG_PATH": tmp_path}):
        client = TestClient(app)
        response = client.get("/service_logs")

    # Cleanup temp file
    os.remove(tmp_path)

    # Assertions
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) == 2

    assert data[0]["service"] == "auth"
    assert data[0]["status"] == "200"
    assert data[0]["content_length"] == "123"

    assert data[1]["service"] == "db"
    assert data[1]["status"] == "500"
    assert data[1]["content_length"] == "0"

