import asyncio
import datetime
import json
import os
import platform
import socket
import time
import aiofiles
import aiohttp
import psutil
import uvicorn
import csv
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi import __version__ as fastapi_version
from contextlib import asynccontextmanager

# Local application imports
from .utils import build_request_headers, validate_and_probe_subnet


load_dotenv() # loads those secretz

CACHE_PATH = os.getenv("CACHE_PATH")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL")) #every 5 min poll services.

async def run_async_health_check():
    #Attempt to load the json services file
    try:
        with open(os.getenv("CONFIG_PATH"), "r") as file:
            services = json.load(file)["services"]
    except Exception as e:
        print(f"Error from healthcheck {e}")
        return
    #Set up results dictionary with a timestamp
    results = { 
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(), 
        "services": {}
    }
    #looping through the service to make a request and we create a non-block event loop to poll the services without locking up the fast api.
    #results are saved in the corresponding service['name'] key.
    async with aiohttp.ClientSession() as session:
        for service in services:
            try:
                async with session.get(
                    service["URL"],
                    ssl=service["TLS"],
                    headers=build_request_headers(service["headers"])
                ) as resp:
                    results["services"][service["name"]] = {
                        "status": resp.status,
                        "content_length": resp.content_length,
                    }
            except Exception as e:
                results["services"][service["name"]] = {
                    "error": str(e)
                }

    # save results to cache
    try:
        with open(CACHE_PATH, "w") as cache:
            json.dump(results, cache, indent=4) # dump the results into the cache (CACHE_PATH)
    except Exception as e:
        print(f"[HealthCheck] Failed to write cache: {e}")

    #save results to log file in CSV format.
    try:
        with open(os.getenv("LOG_PATH"), "a", newline='') as csv_file:
            fieldnames = ["timestamp", "service", "status", "content_length"]
            log_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            for name, data in results["services"].items():
                log_writer.writerow({'timestamp': results["timestamp"],
                                    'service': name,
                                    'status': data.get('status'),
                                    'content_length': data.get('content_length')})
    except Exception as e:
        print(f"Error: {e}")

async def health_check_loop():
    while True:
        print("Triggering Health Check")
        await run_async_health_check()
        print(f"Sleeping for {CHECK_INTERVAL} seconds")
        await asyncio.sleep(CHECK_INTERVAL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting Lifespan create task")
    task = asyncio.create_task(health_check_loop())

    yield
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass



app = FastAPI(lifespan=lifespan)
start_time = datetime.datetime.now(datetime.UTC)

@app.get("/",
    summary="Welcome to the Probapi (Probe API). This is a playground for messing with Fast API"
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



@app.get("/probe/services", summary="Retrieves the contents of the last service probe from cached file. \
    To update which services will be tested every CHECK_INTERVAL seconds update the homelab_services.json via a post request to /probe/update_homelab_services or update the file in the codebase")
def get_services():
    try:
        with open(os.getenv("CACHE_PATH")) as file:
            return json.load(file)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Unable to load service config")

@app.delete("/probe/services/{name}", summary="This endpoint deletes the given service name from the homelab_services.json file. Wait till the next health probe before seeing the latest changes.")
def delete_service(name: str, description="Name of the service in the homelab_services.json It must be the exact key of the service name."):
    config_path = os.getenv("CONFIG_PATH")

    try:
        with open(config_path) as file:
            data = json.load(file)
    except Exception as e:
        raise HTTPException(status_code=500, detail="unable to load service config path")
    
    result = data["services"].pop(name, None)
    
    if result == None:
        raise HTTPException(status_code=404, detail="Service was not found in dictionary.")
    with open(config_path, "w") as file:
        json.dump(data, file, indent = 4)

    return {"status": "deleted", "service": f"{name}"}



@app.post("/probe/update_services",
    summary="This endpoint accepts properly formatted JSON files to overwrite the homelab_services.json file.",
    response_description="Status of the uploaded .json file."
)
async def update_services(file: UploadFile):
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



@app.get("/probe/url")
async def probe_url(
    count: int = Query(...),
    url: str = Query(...),
    ssl: bool = Query(False),
    delay: int = Query(15),
    back_off: int = Query(5)
):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail=f"Error please provide the full URL of the web app to test. i.e. http://{os.getenv('APP_DNS')}:{os.getenv('PORT')}")

    responses = {}

    async with httpx.AsyncClient(verify=ssl) as client:
        for counter in range(count):
            response = await client.get(url)
            responses[counter] = {
                "Type": "GET",
                "URL": url,
                "status": response.status_code,
                "content_length": len(response.content)
            }

            if not response.is_success:
                delay *= back_off

            await asyncio.sleep(delay)

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

@app.get("/service_logs", summary="Retrieves the logs of past health checks done on the services in CSV format. \
    ")
def get_logs():
    try:
        with open(os.getenv("LOG_PATH")) as file:
            reader = csv.DictReader(file)
            return list(reader)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Unable to load csv log file.")

