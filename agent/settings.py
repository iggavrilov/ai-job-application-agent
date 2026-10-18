import json
from pathlib import Path
import yaml
from dotenv import load_dotenv
from pydantic import ValidationError
from agent.models import Candidate
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


def rules():
    config = yaml.safe_load((ROOT / 'config/search_rules.yaml').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('search_rules.yaml must contain a mapping')
    for key in ('include_titles', 'exclude_titles'):
        if not isinstance(config.get(key), list) or not all(isinstance(x, str) and x.strip() for x in config[key]):
            raise ValueError(f'{key} must be a list of nonempty strings')
    weights = config.get('weights', {})
    expected = {'technical', 'experience', 'geography', 'salary', 'additional'}
    if set(weights) != expected or any(not isinstance(v, (int, float)) or v < 0 for v in weights.values()):
        raise ValueError('weights must specify five nonnegative numeric criteria')
    if sum(v for k, v in weights.items() if k != 'salary') <= 0:
        raise ValueError('Non-salary weights must have positive total')
    if sum(weights[key] for key in ('technical','experience','additional')) <= 0:
        raise ValueError('Professional criteria must have a positive total weight')
    if config.get('minimum_salary') is not None:
        raise ValueError('Keep minimum_salary null: currency-aware salary preferences are not implemented')
    interval = config.get('request_interval_seconds', 1)
    if not isinstance(interval, (int, float)) or interval < 0:
        raise ValueError('request_interval_seconds must be nonnegative')
    return config


def profile():
    path = ROOT / 'candidate_profile.json'
    try:
        # Windows PowerShell commonly writes UTF-8 with a BOM.
        content = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        raise ValueError('Missing local candidate_profile.json; follow WINDOWS_SETUP.md to create it from your real CV') from None
    except (OSError, UnicodeError):
        raise ValueError('Cannot read local candidate_profile.json; check permissions and UTF-8 encoding') from None
    try:
        return Candidate.model_validate_json(content)
    except ValidationError:
        # Pydantic errors include input values, which can contain private CV facts.
        raise ValueError('candidate_profile.json does not match the Candidate schema; see WINDOWS_SETUP.md') from None


def extract_pdf(path):
    """Local extraction only. Never transmitted to employer endpoints."""
    import pymupdf
    with pymupdf.open(path) as doc:
        return '\n'.join(page.get_text() for page in doc)
