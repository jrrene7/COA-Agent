import ipaddress
import os
import re
import logging
import socket
from datetime import datetime
from typing import Dict
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv
from tavily import TavilyClient

from lib.logging_config import redact_text
from lib.tooling import tool

load_dotenv("config.env")

logger = logging.getLogger(__name__)

_SCRAPE_TIMEOUT = 10
_MAX_CONTENT_CHARS = 4000
_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 3

# Checked in order; the first match wins and names itself in the error.
_BLOCKED_ADDRESS_KINDS = (
    ("is_loopback", "loopback address"),
    ("is_link_local", "link-local address"),
    ("is_private", "private address range"),
    ("is_reserved", "reserved address"),
    ("is_multicast", "multicast address"),
    ("is_unspecified", "unspecified address"),
)


def _assert_public_address(raw_ip: str, hostname: str) -> None:
    """Reject anything that isn't a routable public address.

    The model chooses the URLs this tool fetches, so without this a crafted or
    merely mistaken lead URL reaches cloud metadata endpoints (169.254.169.254),
    localhost services, and anything else on the internal network.
    """
    address = ipaddress.ip_address(raw_ip)
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped

    for attribute, description in _BLOCKED_ADDRESS_KINDS:
        if getattr(address, attribute, False):
            raise ValueError(
                f"'{hostname}' resolves to {address} — {description} is not allowed."
            )


def _resolve_host(hostname: str, port: int) -> list:
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve '{hostname}': {exc}") from exc
    return [info[4][0] for info in infos]


def _validate_url(url: str) -> str:
    """Raise ValueError if URL is not safe to fetch.

    Residual risk: the host is resolved here and resolved again by the HTTP
    client when it connects, so a DNS entry that flips between the two answers
    (rebinding) can still slip through. Closing that fully means pinning the
    validated address into the connection via a custom transport adapter; an
    egress proxy or network-level policy is the more usual control.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed.")
    if not parsed.hostname:
        raise ValueError("URL must include a valid hostname.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    for raw_ip in _resolve_host(parsed.hostname, port):
        _assert_public_address(raw_ip, parsed.hostname)

    return url


def _fetch_validated(url: str, headers: dict):
    """Fetch `url`, validating every redirect hop rather than trusting the first.

    requests' own redirect following would happily walk from a public site to a
    private address, which is exactly the bypass the scheme check never saw.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        _validate_url(current)
        response = requests.get(
            current,
            headers=headers,
            timeout=_SCRAPE_TIMEOUT,
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location", "")
            if not location:
                raise ValueError("redirect response carried no Location header")
            current = urljoin(current, location)
            continue
        return response, current

    raise ValueError(f"exceeded {_MAX_REDIRECTS} redirects")


@tool
def web_search(query: str, max_results: int = 5) -> Dict:
    """Search the web for current information about a company or topic.

    args:
        query (str): the search query
        max_results (int): number of results to return — default 5
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return {"error": "TAVILY_API_KEY is not set.", "results": [], "answer": ""}

    # Sanitise: strip control characters from query
    query = re.sub(r"[\x00-\x1f\x7f]", " ", query).strip()
    if not query:
        return {"error": "Empty search query.", "results": [], "answer": ""}

    max_results = max(1, min(int(max_results), 10))

    try:
        client = TavilyClient(api_key=api_key)
        result = client.search(
            query=query,
            search_depth="advanced",
            include_answer=True,
            max_results=max_results,
        )
        return {
            "answer": result.get("answer", ""),
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                }
                for r in result.get("results", [])
            ],
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as exc:
        message = redact_text(str(exc))
        logger.error("web_search failed: %s", message)
        return {"error": message, "results": [], "answer": ""}


@tool
def scrape_website(url: str) -> Dict:
    """Fetch and return the visible text content of a website.

    args:
        url (str): the full URL to scrape
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; OutreachBot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        response, final_url = _fetch_validated(url, headers)
        response.raise_for_status()

        # Strip HTML tags and collapse whitespace
        text = re.sub(r"<[^>]+>", " ", response.text)
        text = re.sub(r"\s+", " ", text).strip()

        return {"url": final_url, "content": text[:_MAX_CONTENT_CHARS], "status": "ok"}
    except ValueError as exc:
        logger.warning("scrape_website blocked url=%s reason=%s", url, exc)
        return {"url": url, "content": "", "status": f"invalid url: {exc}"}
    except requests.exceptions.Timeout:
        return {"url": url, "content": "", "status": "timeout"}
    except requests.exceptions.TooManyRedirects:
        return {"url": url, "content": "", "status": "too many redirects"}
    except requests.exceptions.HTTPError as exc:
        return {"url": url, "content": "", "status": f"http error: {exc.response.status_code}"}
    except Exception as exc:
        message = redact_text(str(exc))
        logger.error("scrape_website failed for %s: %s", url, message)
        return {"url": url, "content": "", "status": message}
