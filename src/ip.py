import ipaddress
import logging
from typing import Dict, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

URL = "http://ip-api.com/json/"
PARAMS = "country,regionName,city,isp,proxy"
CLI = ["curl", "Wget", "wget"]

logger = logging.getLogger("myip")
logging.basicConfig(level=logging.INFO)

# Return geolocation IP info
def lookup_geo_info(ip: str) -> Dict:
    try:
        url = URL + ip + "?fields=" + PARAMS
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        # Check if API returned an error
        if data.get('status') == 'fail':
            logger.warning(f"Geolocation API failed for {ip}: {data.get('message', 'Unknown error')}")
            return {}

    except requests.RequestException as e:
        logger.error(f"Failed to fetch geolocation data for {ip}: {str(e)}")
        return {}

# def get_ipv4_from_header(ip: str) -> Optional[str]:
#     try:
#         ip_obj = ipaddress.ip_address(ip)
#         if ip_obj.version == 4:
#             logger.info(f"IPv4 address found: {ip}")
#             return ip

#         if ip_obj.version == 6 and ip_obj.ipv4_mapped:
#             logger.info(f"IPv4-mapped IPv6 address found: {ip} -> {ip_obj.ipv4_mapped}")
#             return str(ip_obj.ipv4_mapped)
#     except ValueError:
#         logger.warning(f"Invalid IP address encountered: {ip}")
#     return None

def get_valid_ip_from_header(ip: str) -> Optional[str]:
    """
    Extract valid IP address from header value, supporting both IPv4 and IPv6
    """
    try:
        ip = ip.strip()
        ip_obj = ipaddress.ip_address(ip)
        
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
            logger.debug(f"Skipping private/local IP: {ip}")
            return None
        
        # Accept both IPv4 and public IPv6 addresses
        if ip_obj.version == 4:
            logger.info(f"Valid IPv4 address found: {ip}")
            return ip
        elif ip_obj.version == 6:
            # Handle IPv4-mapped IPv6 addresses
            if ip_obj.ipv4_mapped:
                mapped_ipv4 = str(ip_obj.ipv4_mapped)
                logger.info(f"IPv4-mapped IPv6 address found: {ip} -> {mapped_ipv4}")
                return mapped_ipv4
            else:
                # Accept native IPv6 addresses
                logger.info(f"Valid IPv6 address found: {ip}")
                return ip
                
    except ValueError:
        logger.warning(f"Invalid IP address encountered: {ip}")
    
    return None


def lookup_ip(req: Request) -> str:
    cf_ip: Optional[str] = req.headers.get("cf-connecting-ip")
    if cf_ip:
        logger.info(f"CF-Connecting-IP header found: {cf_ip}")
        valid_ip = get_valid_ip_from_header(cf_ip)
        if valid_ip:
            return valid_ip

    visit_ip: Optional[str] = req.headers.get("x-forwarded-for")
    if visit_ip:
        logger.info(f"X-Forwarded-For header found: {visit_ip}")
        for ip in visit_ip.split(","):
            ip = ip.strip()
            valid_ip = get_valid_ip_from_header(ip)
            if valid_ip:
                return valid_ip
    
    #Check other header names
    for header in ["x-real-ip", "x-client-ip"]:
        header_ip = req.headers.get(header)
        if header_ip:
            logger.info(f"{header} header found: {header_ip}")
            valid_ip = get_valid_ip_from_header(header_ip)
            if valid_ip:
                return valid_ip
            
    client_host = req.client.host if req.client else "127.0.0.1"
    logger.info(f"Falling back to client host: {client_host}")

    valid_ip = get_valid_ip_from_header(client_host)
    if valid_ip:
        return valid_ip
    
    return client_host


# Check if cli tools are used
def is_cmd(result: Dict[str, str]) -> bool:
    try:
        user_agent: str = result.get("user_agent", "")
        return any(cli in user_agent for cli in CLI)
    except (TypeError, AttributeError):
        return False


def index(req: Request) -> Dict[str, str]:
    user_agent: str = req.headers.get("user-agent", "")
    ip = lookup_ip(req)
    record = lookup_geo_info(ip)
    logger.info(f"Geo-IP lookup for {ip}: {record}")
    return {
        "ip": ip,
        "city": record.get("city", "Unknown"),
        "region_name": record.get("regionName", "Unknown"),
        "country": record.get("country", "Unknown"),
        "isp": record.get("isp", "Unknown"),
        "user_agent": user_agent,
        "proxy": "Yes" if record.get("proxy") is True else "No",
    }


@app.get("/")
def return_html_page(req: Request):
    result = index(req)
    if is_cmd(result):
        logger.info(f"CLI detected, returning plain IP: {result.get('ip')}")
        return PlainTextResponse(content=result.get("ip"))

    return templates.TemplateResponse(
        request=req, name="index.html", context=result, status_code=status.HTTP_200_OK
    )


@app.get("/json")
def json_page(req: Request) -> Dict[str, str]:
    return index(req)


@app.exception_handler(404)
def not_found(req: Request):
    return templates.TemplateResponse(
        request=req, name="404.html", status_code=status.HTTP_404_NOT_FOUND
    )


if __name__ == "__main__":
    uvicorn.run("ip:app", host="0.0.0.0", reload=False, port=8500, log_level="info")
