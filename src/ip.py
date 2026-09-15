import ipaddress
import logging
import os
import time

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


PORT = int(os.getenv("MYIP_PORT", "8500"))
LOG_LEVEL = os.getenv("MYIP_LOG_LEVEL", "INFO").upper()
# When false, forwarding headers (X-Forwarded-For, ...) are ignored and the TCP
# peer address is used. Keep true behind Cloudflare/Caddy, set false when the
# port is exposed directly.
TRUST_PROXY = os.getenv("MYIP_TRUST_PROXY", "true").strip().lower() in {"1", "true", "yes", "on"}
# Requests per minute per client for arbitrary `?ip=` lookups. 0 disables the limit.
LOOKUP_RATE_LIMIT = int(os.getenv("MYIP_LOOKUP_RATE_LIMIT", "30"))
GEO_TIMEOUT = 5  # seconds
GEO_CACHE_TTL = 300  # seconds a geolocation result is reused
GEO_CACHE_MAX = 1000  # entries before the cache is flushed

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

URL = "http://ip-api.com/json/"
PARAMS = (
    "status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,"
    "isp,org,as,proxy,hosting,mobile"
)
CLI = ["curl", "wget"]

logger = logging.getLogger("myip")
logging.basicConfig(level=LOG_LEVEL)

# ip -> (expiry monotonic timestamp, geo record)
_geo_cache: dict[str, tuple[float, dict]] = {}
# client ip -> (current minute window, request count in that window)
_rate_windows: dict[str, tuple[int, int]] = {}


def is_public_ip(ip: str) -> bool:
    """
    Return True if the given string is a valid public IP address.

    Private, loopback, link-local, reserved, multicast and unspecified
    addresses are not routable on the public internet.
    """
    try:
        ip_obj = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return not (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def is_rate_limited(key: str) -> bool:
    """Fixed-window per-minute rate limit for `?ip=` lookups."""
    if LOOKUP_RATE_LIMIT <= 0:
        return False
    window = int(time.time() // 60)
    start, count = _rate_windows.get(key, (window, 0))
    if start != window:
        start, count = window, 0
    count += 1
    if len(_rate_windows) >= 10_000:  # ponytail: crude flush, fine for one process
        _rate_windows.clear()
    _rate_windows[key] = (start, count)
    return count > LOOKUP_RATE_LIMIT


# Return geolocation IP info
def lookup_geo_info(ip: str) -> dict:
    if not is_public_ip(ip):
        logger.debug(f"Skipping geolocation lookup for non-public IP: {ip}")
        return {}

    now = time.monotonic()
    cached = _geo_cache.get(ip)
    if cached and cached[0] > now:
        logger.debug(f"Geolocation cache hit for {ip}")
        return cached[1]

    try:
        url = URL + ip + "?fields=" + PARAMS
        response = requests.get(url, timeout=GEO_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "fail":
            logger.warning(f"Geolocation API failed for {ip}: {data.get('message', 'Unknown error')}")
            return {}

        if len(_geo_cache) >= GEO_CACHE_MAX:  # ponytail: crude flush, fine for one process
            _geo_cache.clear()
        _geo_cache[ip] = (now + GEO_CACHE_TTL, data)
        return data

    except requests.RequestException as e:
        logger.error(f"Failed to fetch geolocation data for {ip}: {e!s}")
        return {}
    except Exception as e:  # noqa: BLE001 - keep the page resilient to unexpected upstream errors
        logger.error(f"Unexpected error during geolocation lookup for {ip}: {e!s}")
        return {}


def get_valid_ip_from_header(ip: str) -> str | None:
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
    if not TRUST_PROXY:
        client_host = req.client.host if req.client else "127.0.0.1"
        return get_valid_ip_from_header(client_host) or client_host

    cf_ip: str | None = req.headers.get("cf-connecting-ip")
    if cf_ip:
        logger.info(f"CF-Connecting-IP header found: {cf_ip}")
        valid_ip = get_valid_ip_from_header(cf_ip)
        if valid_ip:
            return valid_ip

    visit_ip: str | None = req.headers.get("x-forwarded-for")
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

    # No trusted proxy header was found, so report the TCP peer address as-is.
    # It may be private/loopback (e.g. local development); when the peer is
    # public, get_valid_ip_from_header still normalizes IPv4-mapped IPv6.
    client_host = req.client.host if req.client else "127.0.0.1"
    logger.info(f"Falling back to client host: {client_host}")

    valid_ip = get_valid_ip_from_header(client_host)
    if valid_ip:
        return valid_ip

    return client_host


# Check if cli tools are used
def is_cmd(user_agent: str) -> bool:
    return any(cli in user_agent.lower() for cli in CLI)


def build_map_url(lat, lon) -> str | None:
    if lat is None or lon is None:
        return None
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=10/{lat}/{lon}"


def index(req: Request, requested_ip: str | None = None) -> dict[str, object]:
    client_ip = lookup_ip(req)
    ip = client_ip
    if requested_ip:
        requested_ip = requested_ip.strip()
        if not is_public_ip(requested_ip):
            raise HTTPException(status_code=400, detail="Invalid or non-public IP address")
        if is_rate_limited(client_ip):
            raise HTTPException(status_code=429, detail="Too many requests")
        ip = requested_ip

    user_agent: str = req.headers.get("user-agent", "")
    record = lookup_geo_info(ip)
    logger.info(f"Geo-IP lookup for {ip}: {record}")

    lat = record.get("lat")
    lon = record.get("lon")
    return {
        "ip": ip,
        "city": record.get("city", "Unknown"),
        "region_name": record.get("regionName", "Unknown"),
        "country": record.get("country", "Unknown"),
        "country_code": record.get("countryCode", "Unknown"),
        "zip": record.get("zip", "Unknown"),
        "lat": lat,
        "lon": lon,
        "timezone": record.get("timezone", "Unknown"),
        "isp": record.get("isp", "Unknown"),
        "org": record.get("org", "Unknown"),
        "asn": record.get("as", "Unknown"),
        "user_agent": user_agent,
        "map_url": build_map_url(lat, lon),
        "proxy": "Yes" if record.get("proxy") is True else "No",
        "hosting": "Yes" if record.get("hosting") is True else "No",
        "mobile": "Yes" if record.get("mobile") is True else "No",
    }


@app.get("/")
def return_html_page(req: Request, ip: str | None = Query(default=None)):
    # CLI tools only want the bare IP, so skip the geolocation call entirely.
    if ip is None and is_cmd(req.headers.get("user-agent", "")):
        logger.info("CLI detected, returning plain IP")
        return PlainTextResponse(content=lookup_ip(req))

    result = index(req, requested_ip=ip)
    return templates.TemplateResponse(
        request=req, name="index.html", context=result, status_code=status.HTTP_200_OK
    )


@app.get("/ip")
def ip_page(req: Request) -> PlainTextResponse:
    return PlainTextResponse(content=lookup_ip(req))


@app.get("/json")
def json_page(req: Request, ip: str | None = Query(default=None)) -> dict[str, object]:
    return index(req, requested_ip=ip)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(404)
def not_found(req: Request, exc):
    return templates.TemplateResponse(
        request=req, name="404.html", status_code=status.HTTP_404_NOT_FOUND
    )


if __name__ == "__main__":
    uvicorn.run("ip:app", host="0.0.0.0", reload=False, port=PORT, log_level="info")
