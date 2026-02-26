import os
import pytest
from dotenv import load_dotenv
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from app.main import app
from app.utils import build_request_headers, validate_and_probe_subnet


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

def test_probe_url_required_query_parameters():
    response = client.get("/probe/url")
    data = response.json()
    assert response.status_code == 422
    assert isinstance(data["detail"], list) # if request does not contain required parameters 
    assert len(data["detail"])== 2 #currently there are two required query parameters

# def test_probe_homelab_service_health():
#     response = client.get("/probe/homelab_service_health")
#     assert response.status_code == 200

@patch("app.main.time.sleep")
@patch("app.main.requests.get")
def test_probe_url_request_count(mock_get, mock_sleep):
    #creating a mock object to mimick get request and time.sleep calls. 
    # In the test we count the number of times they were called to check the underlying logic
    mock_response = MagicMock()
    mock_response.ok = False
    mock_response.status_code = 500
    mock_response.content = b"error"
    mock_get.return_value = mock_response

    response = client.get("/probe/url", params={
        "count": 2,
        "url": f"http://{os.getenv('APP_DNS')}:8000",
        "delay": 2, 
        "back_off": 3,
        "ssl": True
    })

    assert response.status_code == 200
    assert mock_sleep.call_count == 2
    
    response = client.get("/probe/url", params={
        "count": 10,
        "url": f"http://{os.getenv('APP_DNS')}:{os.getenv('PORT')}",
        "delay": 2, 
        "back_off": 3,
    })

    assert response.status_code == 200
    #call_count should be total of count between the 2 requests
    assert mock_sleep.call_count == 12


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


def test_subnet_not_class_c_low():
    with pytest.raises(HTTPException) as exc:
        validate_and_probe_subnet("10.0.0.0/24")

    assert exc.value.status_code == 400
    assert "Class C" in exc.value.detail


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

    