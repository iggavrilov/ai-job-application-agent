"""Source registry validation and health view helpers."""
from pathlib import Path
from urllib.parse import urlsplit
import yaml
from agent.settings import ROOT


def load_discovery_sources(path=None):
    path = Path(path or ROOT / "config/discovery_sources.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError("discovery_sources.yaml must contain a sources list")
    valid, errors, seen = [], [], set()
    for index, item in enumerate(data["sources"], 1):
        try:
            required = ("id", "name", "category", "source_type", "official_url", "coverage",
                        "public_api", "authentication", "permitted_access", "rate_limit", "status")
            if any(not item.get(key) for key in required):
                raise ValueError("missing required registry fields")
            if item["id"] in seen:
                raise ValueError("duplicate source id")
            seen.add(item["id"])
            for key in ("official_url", "evidence_url", "terms_url", "api_url"):
                value = item.get(key)
                if value and (urlsplit(value).scheme != "https" or not urlsplit(value).hostname):
                    raise ValueError("source URLs must use HTTPS")
            if item["status"] == "integrated" and item["source_type"] == "aggregator" and not item.get("api_url"):
                raise ValueError("integrated API source has no endpoint")
            if item["status"] == "integrated" and not item.get("evidence_url"):
                raise ValueError("integrated source has no official evidence")
            valid.append(item)
        except (AttributeError, TypeError, ValueError):
            errors.append(f"Source registry entry {index} is invalid; review its official URL, policy, and integration status")
    return valid, errors


def active_api_sources(path=None):
    sources, errors = load_discovery_sources(path)
    enabled = [source for source in sources if source.get("enabled") and source.get("status") == "integrated"
               and source.get("source_type") == "aggregator"]
    return enabled, errors
