from typing import Dict, Optional

import requests
import uvicorn
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

URL = "http://ip-api.com/json/"
PARAMS = "country,regionName,city,isp,proxy"
CLI = ["curl", "Wget", "wget"]


# Return geolocation IP info
def lookup_geo_info(ip: str) -> Dict:
    try:
        url = URL + ip + "?fields=" + PARAMS
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch geolocation data: {str(e)}")


def lookup_ip(req: Request) -> str:
    visit_ip: Optional[str] = req.headers.get("x-forwarded-for")
    # Get first IP only
    ip = visit_ip.split(",")[0] if visit_ip is not None else req.client.host
    return ip


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
