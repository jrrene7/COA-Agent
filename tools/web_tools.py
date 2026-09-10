import os
import re
import logging
from datetime import datetime
from typing import Dict
from urllib.parse import urlparse

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


def _validate_url(url: str) -> str:
    """Raise ValueError if URL is not safe to fetch."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed.")
    if not parsed.netloc:
        raise ValueError("URL must include a valid hostname.")
    return url


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
    try:
        _validate_url(url)
    except ValueError as exc:
        return {"url": url, "content": "", "status": f"invalid url: {exc}"}

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; OutreachBot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        response = requests.get(
            url,
            headers=headers,
            timeout=_SCRAPE_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()

        # Strip HTML tags and collapse whitespace
        text = re.sub(r"<[^>]+>", " ", response.text)
        text = re.sub(r"\s+", " ", text).strip()

        return {"url": url, "content": text[:_MAX_CONTENT_CHARS], "status": "ok"}
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
