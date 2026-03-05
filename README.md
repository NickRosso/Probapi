# Probapi

This is a small FastAPI project I built for experimenting with network probing inside my homelab. It started as a way to play with FastAPI, but it turned into a handy little tool for checking HTTP endpoints, scanning subnets, and more to come.
It’s useful when you don’t need Prometheus‑level complexity but still want something that can ping your stuff, log failures, and generally let you know when things go sideways.

---
## Pre-reqs
Docker installed... duh

## Update Env File
Copy .example_env to .env and fill in your values.
Environment variables referenced in homelab_services.json must match the names in key in the .env. If they don’t, any authentication bearer tokens etc wont work and you’ll spend 20 minutes debugging something that was spelled wrong.

## Startup

```bash
docker compose build
docker compose up
```

---

## Features

### System Info (`GET /`)
This endpoint returns a bunch of system details so you can confirm the API is alive and also judge how badly your server is doing. It includes:

- Python, FastAPI, and Uvicorn versions  
- Hostname  
- Uptime  
- CPU load  
- Memory and disk usage  
- Current UTC time and epoch time  


---

## Background Service Health Checks

Probapi runs a background task using FastAPI’s lifespan system. Every `CHECK_INTERVAL` seconds, it:

1. Loads the services from `homelab_services.json`
2. Sends GET requests to each one
3. Writes the results to:
   - A JSON cache file (`CACHE_PATH`)
   - A CSV log file (`LOG_PATH`)

This loop runs forever (or until you shut down the container). TIts kinda like cron job that never asked to exist but is doing its best anyway. It uses the FastAPI Lifespan context manager instead of the onstart event which is clunkier but I guess not deprecated so thats cool.

---

### Cached Service Results (`GET /probe/services`)
This endpoint returns the most recent health-check results. It does not run a live check. The background loop handles that part.

Example response:

```json
{
  "timestamp": "2025-02-25T03:15:22.123456Z",
  "services": {
    "my_service": {
      "status": 200,
      "content_length": 5123
    },
    "another_service": {
      "error": "Connection failed"
    }
  }
}
```

## Probapi Services (POST /probe/update_services)
Uploads a new homelab_services.json file. The endpoint:

  - Validates the JSON
  - Ensures required keys exist
  - Creates a timestamped backup
  - Replaces the existing config

If you upload garbage, it will tell you. If you upload valid JSON, it will happily overwrite your config, so double-check before you hit send.

Delete a Service (DELETE /probe/services/{name})
Deletes a service from the config file.
Changes show up on the next background health-check cycle.

---

### HTTP Probe (`GET /probe/url`)
Sends repeated GET requests to a URL you provide. You can control:

- Number of requests  
- SSL verification  
- Delay between requests  
- Backoff multiplier when a request fails  

It’s handy for stress testing or poking a flaky service until it behaves (or doesn’t).

---

### Subnet Probe (`GET /probe/subnet`)
Give it a Class C subnet like 192.168.1.0/28 and it’ll run an ICMP ping sweep across all hosts.
The subnet validation and ping logic live in utils.py, which is where all the fun networking stuff hides.

The subnet validation and ping logic lives in `utils.py`.



### Service Logs (GET /service_logs)
Returns the CSV log file as a list of JSON objects.
Each row includes:
  - timestamp
  - service
  - status
  - content_length

