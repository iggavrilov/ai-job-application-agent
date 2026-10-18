import json
from pathlib import Path
import yaml
from dotenv import load_dotenv
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
    if config.get('minimum_salary') is not None:
        raise ValueError('Keep minimum_salary null: currency-aware salary preferences are not implemented')
    interval = config.get('request_interval_seconds', 1)
    if not isinstance(interval, (int, float)) or interval < 0:
        raise ValueError('request_interval_seconds must be nonnegative')
    return config


def profile():
    return Candidate.model_validate_json((ROOT / 'candidate_profile.json').read_text(encoding='utf-8'))


def extract_pdf(path):
    """Local extraction only. Never transmitted to employer endpoints."""
    import pymupdf
    with pymupdf.open(path) as doc:
        return '\n'.join(page.get_text() for page in doc)
