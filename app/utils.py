import ipaddress
import os
import subprocess
from dotenv import load_dotenv
from fastapi import HTTPException


load_dotenv() # loads those secret

def validate_and_probe_subnet(subnet):
    try: 
        network = ipaddress.ip_network(subnet, strict=True) 
    except ValueError: 
        raise HTTPException(status_code=400, detail="Invalid subnet format. Use CIDR notation like 192.168.1.0/28.")
    #TODO: this first octet check does not appear to be working based on unit test coverage
    first_octet = int(str(network.network_address).split(".")[0])
    if not (192 <= first_octet <= 223):
        raise HTTPException(status_code=400, detail="Subnet must be in the Class C range (192.0.0.0 – 223.255.255.255).")

    # Validate prefix length (must be /24 or smaller)
    #TODO: this first prefix check does not appear to be working based on unit test coverage
    if network.prefixlen < 24:
        raise HTTPException(status_code=400, detail="Subnet must be /24 or smaller (e.g., /24, /25, /26, /27, /28).")
    
    # expand hosts
    hosts = list(network.hosts())

    # placeholder for ping results
    alive_hosts = []

    # example ICMP ping (Linux/macOS)
    for host in hosts:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", str(host)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if result.returncode == 0:
            alive_hosts.append(str(host))
    return{
        "subnet": subnet,
        "total_hosts": len(hosts),
        "alive_hosts": alive_hosts
    }


def build_request_headers(header_list):
    headers = {}
    for item in header_list:
        if ":" not in item:
            continue  # skip invalid entries

        key, value = item.split(":", 1)
        key = key.strip()
        value = value.strip()

        # If the value matches an env var name, replace it
        env_value = os.getenv(value)
        if env_value is not None:
            value = env_value

        headers[key] = value

    return headers
