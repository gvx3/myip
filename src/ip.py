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
        return response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch geolocation data: {str(e)}")

def get_ipv4_from_header(ip: str) -> Optional[str]:
    try:
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.version == 4:
            logger.info(f"IPv4 address found: {ip}")
            return ip

        if ip_obj.version == 6 and ip_obj.ipv4_mapped:
            logger.info(f"IPv4-mapped IPv6 address found: {ip} -> {ip_obj.ipv4_mapped}")
            return str(ip_obj.ipv4_mapped)
    except ValueError:
        logger.warning(f"Invalid IP address encountered: {ip}")
    return None



def lookup_ip(req: Request) -> str:
    cf_ip: Optional[str] = req.headers.get("cf-connecting-ip")
    if cf_ip:
        logger.info(f"CF-Connecting-IP header found: {cf_ip}")
        ipv4 = get_ipv4_from_header(cf_ip)
        if ipv4:
            return ipv4

    visit_ip: Optional[str] = req.headers.get("x-forwarded-for")
    # Get first IP only
    # ip = visit_ip.split(",")[0] if visit_ip is not None else req.client.host
    if visit_ip:
        logger.info(f"X-Forwarded-For header found: {visit_ip}")
        for ip in visit_ip.split(","):
            ip = ip.strip()
            ipv4 = get_ipv4_from_header(ip)
            if ipv4:
                return ipv4
    
    logger.info(f"Falling back to client host: {req.client.host}")
    return req.client.host


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
        "city": record.get("city", ""),
        "region_name": record.get("regionName", ""),
        "country": record.get("country", ""),
        "isp": record.get("isp", ""),
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
