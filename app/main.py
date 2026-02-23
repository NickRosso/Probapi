from fastapi import FastAPI, Query, HTTPException, UploadFile
from fastapi import __version__ as fastapi_version
from .utils import validate_and_probe_subnet, build_request_headers
from dotenv import load_dotenv
import aiofiles
import os
import uvicorn
import requests
import time
import platform
import socket
import psutil
import datetime
import json

load_dotenv() # loads those secretz

app = FastAPI()
start_time = datetime.datetime.now(datetime.UTC)

@app.get("/",
    summary="Welcome to the Homelab Probe API. This is a playground for messing with Fast API"
    " and trying different methodologies. This endpoint provides interesting system and server information."
)
def index():
    return{
        "app": {
            "name": "HomeLab Probe API",
            "version": "1.0.0",
        },
        "server": {
            "python_version": platform.python_version(),
            "fastapi_version": fastapi_version,
            "uvicorn_version": uvicorn.__version__,
            "hostname": socket.gethostname(),
            "uptime_seconds": (datetime.datetime.now(datetime.UTC) - start_time).seconds,
            "current_time_utc": datetime.datetime.now(datetime.UTC).isoformat(),
            "epoch_time": int(time.time())
        }, 
        "system": { 
            "cpu_count": psutil.cpu_count(),
            "cpu_load": psutil.getloadavg(),
            "memory": psutil.virtual_memory()._asdict(),
            "disk_usage": psutil.disk_usage("/")._asdict(), 
            }
        }

@app.get("/probe/homelab_service_health",
    summary="This endpoint makes requests to each of the configured defined in data/homelab_services.json.",
    response_description="Responses from various service health endpoints."
)
def probe_homelab_service_health():
    results = {} 
    try:
        with open("/code/app/data/homelab_services.json", "r") as file:
            data = json.load(file)
            services = data["services"]

            for service in services:
                headers = build_request_headers(service['headers'])
                response = requests.get(service["URL"], verify=service["TLS"], headers=headers)
                results[service["name"]] = response.text

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PossibleError loading homelab_services.json. File is missing or data is invalid JSON. Full trace: {e}")
    return results

@app.post("/probe/update_homelab_services",
    summary="This enpdoint accepts properly formatted JSON files to overwrite the homelab_services.json file.",
    response_description="Status of the uploaded .json file."
)
async def update_homelab_services_file(file: UploadFile):
    contents = await file.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file was empty.")
    
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail=f"Uploaded file must have .json")
    
    #After checking if file ends with .json in the name now we attempt to load it.
    try:
        uploaded_json = json.loads(contents)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Uploaded file does not contain valid json: {e}")
    
    services = uploaded_json["services"]

    if not isinstance(services, list):
        raise HTTPException(status_code=400, detail="top level services key must be a list of objects.")
        
    #JSON keys that should exist for each service in the .json
    required_keys = ["name", "URL", "port", "TLS", "headers"]

    for service in services:
        if not isinstance(service, dict):
            raise HTTPException(status_code=400, detail="service must be a JSON object.")
        for key in required_keys:
            if key in service:
                continue
            else:
                raise HTTPException(status_code=400, detail=f"missing key in uploaded service: {service} key missing: {key}")
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = f"{os.getenv("UPLOAD_DIR")}/homelab_services_backup_{timestamp}.json"
    
    #reads original contents
    async with aiofiles.open(os.getenv("CONFIG_PATH"), "rb") as original:
        existing_contents = await original.read()
    
    #writes a backup of the original contents
    async with aiofiles.open(backup_path, "wb") as backup_file:
        await backup_file.write(existing_contents)
    #Writes a temporary file  using the uploaded documents.
    temp_path = f"{os.getenv("UPLOAD_DIR")}/uploaded_temp_{timestamp}.json" 
    async with aiofiles.open(temp_path, "wb") as temp_file: 
        await temp_file.write(contents)
    
    #replace the existing config with the temp file.
    os.replace(temp_path,os.getenv("CONFIG_PATH"))

    return {
        "Status": "updated", 
        "Filename": file.filename,
        "backup_created": backup_path}

@app.get("/probe/url",
    summary="This endpoint probes the provided web app given with GET requests.",
    response_description="A dictionary of requests detailing request information and results. The key is the" \
    " request counter and the value is the response information."
)
def probe_url(
    count: int = Query(..., description="Number of Requests to Send."),
    url: str = Query(..., description="Full URL including http:// or https://"),
    ssl: bool = Query(False, description="Verify SSL Certificate"),
    delay: int = Query(15, description="Time in seconds between requests"),
    back_off: int = Query(5, description="Increases time between requests each time there is a error")
):

    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Error please provide the full URL of the web app to test. i.e. https://localhost")

    
    responses = {}
    print(f"Making {count} GET request(s)")
    for counter in range(0, count):
        response = requests.get(url, verify=ssl)
        responses[counter] = {
            "Type": "GET", 
            "URL": url,
            "status": response.status_code, 
            "content_length": len(response.content)
        }
        if not response.ok:
            delay *= back_off # Increase delay by back_off
            
        time.sleep(delay)

    return responses
    
@app.get("/probe/subnet",
    summary="This checks and does a ICMP ping on all hosts in a Class C subnet.",
    response_description="Returns all of the IPs that responded"
)
def probe_subnet(
    subnet: str = Query(..., description="Subnet such as 192.168.1.0/28. Must be a Class C Subnet.")
):
    response = validate_and_probe_subnet(subnet)
    return response