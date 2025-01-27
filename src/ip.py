from typing import Dict

import requests
import uvicorn
from fastapi import FastAPI, Request, status
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
    url = URL + ip + "?fields=" + PARAMS
    record: Dict = requests.get(url).json()
    return record


def lookup_ip(req: Request) -> str:
    visit_ip: str = req.headers.get("x-forward-for")
    # Get first IP only
    ip = visit_ip.split(",")[0] if visit_ip is not None else req.client.host
    return ip


# Check if cli tools are used
def is_cmd(result: Dict[str, str]) -> bool:
    try:
        user_agent: str = result.get("user_agent")
        for options in CLI:
            if options in user_agent:
                return True
    except TypeError:
        pass

    return False


def index(req: Request) -> Dict[str, str]:
    user_agent: str = req.headers.get("user-agent")
    ip = lookup_ip(req)
    record = lookup_geo_info(ip)
    country = record.get("country")
    region_name = record.get("regionName")
    isp = record.get("isp")
    city = record.get("city")
    proxy = record.get("proxy")
    proxy = "Yes" if record.get("proxy") is True else "No"

    content: Dict[str, str] = {
        "ip": ip,
        "city": city,
        "region_name": region_name,
        "country": country,
        "isp": isp,
        "user_agent": user_agent,
        "proxy": proxy,
    }
    return content


@app.get("/")
def return_html_page(req: Request):
    result = index(req)
    if is_cmd(result):
        return PlainTextResponse(content=result.get("ip"))

    return templates.TemplateResponse(
        request=req, name="index.html",
        context=result,
        status_code=status.HTTP_200_OK
    )


@app.exception_handler(404)
def not_found(req: Request):
    return templates.TemplateResponse(
        request=req,
        name="404.html",
        status_code=status.HTTP_404_NOT_FOUND
    )


# @app.get("/json")
# def json_page(req: Request):
#     ip = dict(req.headers).get("x-forwarded-for")
#     user_agent = dict(req.headers).get("user-agent")

#     all = dict(req.headers)
#     return {
#         "user-agent": user_agent,
#         "ip": ip,
#         "all": all,
#     }


if __name__ == "__main__":
    uvicorn.run("ip:app", reload="True", port=8000, log_level="info")
