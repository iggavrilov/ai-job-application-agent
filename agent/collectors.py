"""Documented public board APIs; no global search or authenticated access."""
import html
import json
import re
import time
import logging
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import httpx
from agent.models import Job

log = logging.getLogger(__name__)


class RobotsPolicyError(RuntimeError):
    def __init__(self, message, status):
        super().__init__(message)
        self.policy_http_status = status


class CollectedJobs(list):
    """List-compatible results with nonfatal malformed-posting diagnostics."""
    def __init__(self):
        super().__init__()
        self.warnings = []

ENDPOINTS = {
    'greenhouse': 'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true',
    'lever': 'https://api.lever.co/v0/postings/{board}?mode=json',
    'ashby': 'https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true',
}


def plain(value):
    decoded = html.unescape(str(value or ''))
    decoded = re.sub(r'</?(?:p|li|div|h[1-6]|br)\b[^>]*>', '\n', decoded, flags=re.I)
    return re.sub('<[^>]+>', ' ', decoded).strip()


def remote_status(location, explicit=None):
    # Structured source values take precedence over incidental location words.
    value = str(explicit or '').lower()
    if explicit is False or value in ('onsite', 'on-site', 'on site'):
        return 'Onsite'
    if value == 'hybrid':
        return 'Hybrid'
    if explicit is True or value == 'remote':
        return 'Remote'
    value = location.lower()
    if 'hybrid' in value:
        return 'Hybrid'
    is_remote = 'remote' in value or bool(re.search(r'\b(home[- ]based|work from home)\b', value))
    is_onsite = 'onsite' in value or 'on-site' in value or bool(re.search(r'\boffice[- ]based\b', value))
    if is_remote and is_onsite:
        return 'Unknown'  # Alternative placements are not proof of a hybrid contract.
    if is_remote:
        return 'Remote'
    if is_onsite:
        return 'Onsite'
    return 'Unknown'


def normalize(platform, company, item):
    if platform not in ENDPOINTS:
        raise ValueError('Unsupported source platform')
    if platform == 'greenhouse':
        location = item.get('location', {}).get('name') or 'unknown'
        remote = remote_status(location)
        if remote == 'Unknown' and not re.search(r'office[- ]based|home[- ]based',location,re.I):
            mode = re.search(r'\b(?:work(?:place)? type|working arrangement|work mode)\s*:\s*(remote|hybrid|onsite|on-site)\b', plain(item.get('content')), re.I)
            if mode:
                remote = remote_status(location, mode[1])
        return Job(title=item['title'], company=company, location=location,
                   remote=remote, description=item.get('content') or '',
                   url=item['absolute_url'], platform=platform, source_id=str(item['id']),
                   salary=json.dumps(item['pay_input_ranges']) if item.get('pay_input_ranges') else None,
                   published=item.get('first_published'))
    if platform == 'lever':
        categories = item.get('categories') or {}
        location = categories.get('location') or 'unknown'
        lists = item.get('lists') or []
        description = (item.get('description') or '') + '\n' + '\n'.join(x.get('text','') + '\n' + x.get('content','') for x in lists) + '\n' + (item.get('additional') or '')
        from datetime import datetime, timezone
        return Job(title=item['text'], company=company, location=location,
                   remote=remote_status(location, item.get('workplaceType')),
                   description=description, requirements='\n'.join(x.get('content','') for x in lists) or 'unknown',
                   employment_type=categories.get('commitment') or 'unknown',
                   salary=json.dumps(item['salaryRange']) if item.get('salaryRange') else None,
                   published=datetime.fromtimestamp(item['createdAt']/1000, timezone.utc).isoformat() if item.get('createdAt') else None,
                   url=item['hostedUrl'], platform=platform, source_id=str(item['id']))
    location = item.get('location') or 'unknown'
    return Job(title=item['title'], company=company, location=location,
               remote=remote_status(location, item.get('workplaceType') or item.get('isRemote')),
               description=item.get('descriptionHtml') or item.get('descriptionPlain') or '',
               employment_type=item.get('employmentType') or 'unknown',
               salary=json.dumps(item['compensation']) if item.get('compensation') else None,
               published=item.get('publishedAt'), url=item['jobUrl'], platform=platform, source_id=str(item.get('id') or item['jobUrl']))


def check_robots(client, url, interval=1):
    """Establish permission before any API or employer-page request."""
    origin = '{0.scheme}://{0.netloc}'.format(urlsplit(url))
    robots = client.get(origin + '/robots.txt', follow_redirects=False)
    if robots.status_code == 200:
        parser = RobotFileParser()
        parser.parse(robots.text.splitlines())
        if not parser.can_fetch('QAJobAgent', url):
            raise RobotsPolicyError('robots.txt disallows endpoint', robots.status_code)
        interval = max(interval, parser.crawl_delay('QAJobAgent') or 0)
    elif robots.status_code not in (404, 410):
        raise RobotsPolicyError(f'Cannot establish robots policy: {robots.status_code}', robots.status_code)
    time.sleep(interval)
    return robots.status_code


def collect(board, client, interval=1):
    platform = board['platform']
    url = ENDPOINTS[platform].format(board=board['board'])
    check_robots(client, url, interval)
    for _ in range(6):
        response = client.get(url, follow_redirects=False)
        if not response.is_redirect:
            break
        target = urljoin(url, response.headers['location'])
        if urlsplit(target).scheme != 'https':
            raise RuntimeError('Public API redirect must use HTTPS')
        check_robots(client, target, interval)
        url = target
    else:
        raise RuntimeError('Too many public API redirects')
    response.raise_for_status()  # Includes 429: do not retry aggressively.
    data = response.json()
    items = data if platform == 'lever' else data['jobs']
    if not isinstance(items, list):
        raise ValueError('Public API response must contain a jobs list')
    result = CollectedJobs()
    result.http_status = response.status_code
    result.published_count = len(items)
    for index, item in enumerate(items):
        try:
            result.append(normalize(platform, board['company'], item))
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
            message = f'{platform}/{board["board"]}: malformed posting at index {index}; skipped'
            result.warnings.append(message)
            log.warning(message)
    return result
