"""Wikipedia REST API client — free, no auth, always reachable.

Gives the model a grounded company description (history, business segments,
key people) without us having to scrape About-Us pages. Perfect for
"who is this company" context the LLM otherwise has to hallucinate.
"""
from __future__ import annotations

from typing import Optional

import requests

from src.data.cache import get_or_set
from src.utils.logger import get_logger

log = get_logger("tools.wikipedia")

UA = {"User-Agent": "D-R1-Quant/1.0 (research)"}


def wiki_summary(company_name: str) -> dict:
    """REST summary for a company. Tries several title variants."""
    if not company_name:
        return {}
    name = company_name.strip()
    variants = []
    for n in [name,
              name + " (company)",
              name.replace(" Limited", "").replace(" Ltd", "").strip(),
              name.replace(" Limited", "").replace(" Ltd", "").strip() + " (company)"]:
        if n and n not in variants:
            variants.append(n)

    def _do():
        for title in variants:
            t = title.replace(" ", "_")
            try:
                r = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{t}",
                    headers=UA, timeout=12,
                )
                if r.status_code == 200:
                    j = r.json() or {}
                    if j.get("type") == "standard" and j.get("extract"):
                        return {
                            "title": j.get("title"),
                            "description": j.get("description"),
                            "extract": j.get("extract"),
                            "url": (j.get("content_urls") or {})
                                   .get("desktop", {}).get("page"),
                        }
            except Exception as e:
                log.debug(f"wikipedia {title}: {e}")
        return {}

    return get_or_set("wikipedia",
                      company_name.upper().replace(" ", "_")[:80],
                      ttl_seconds=60 * 60 * 24 * 7, fn=_do) or {}
