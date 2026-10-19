"""Documented public board APIs; no global search or authenticated access."""
import html
import json
import re
import time
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin, urlencode, parse_qsl
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
        self.published_count = 0
        self.http_status = None
        self.expired_count = 0
        self.expired_postings = []
        self.pages_fetched = 0
        self.pagination_complete = True

PUBLIC_FEEDS = {
    'arbeitnow': 'https://www.arbeitnow.com/api/job-board-api?page=1',
    'remoteok': 'https://remoteok.com/api',
    'himalayas': 'https://himalayas.app/jobs/api?limit=20',
}


class PublicFeedRateLimited(RuntimeError):
    """Safe marker for a provider rate limit; callers must stop without retrying."""
    policy_http_status = 429

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
                   published=item.get('first_published'), source_name=company, source_type='employer',
                   source_url=item['absolute_url'], employer_application_url=item['absolute_url'],
                   source_checked_at=datetime.now(timezone.utc).isoformat())
    if platform == 'lever':
        categories = item.get('categories') or {}
        location = categories.get('location') or 'unknown'
        lists = item.get('lists') or []
        description = (item.get('description') or '') + '\n' + '\n'.join(x.get('text','') + '\n' + x.get('content','') for x in lists) + '\n' + (item.get('additional') or '')
        return Job(title=item['text'], company=company, location=location,
                   remote=remote_status(location, item.get('workplaceType')),
                   description=description, requirements='\n'.join(x.get('content','') for x in lists) or 'unknown',
                   employment_type=categories.get('commitment') or 'unknown',
                   salary=json.dumps(item['salaryRange']) if item.get('salaryRange') else None,
                   published=datetime.fromtimestamp(item['createdAt']/1000, timezone.utc).isoformat() if item.get('createdAt') else None,
                   url=item['hostedUrl'], platform=platform, source_id=str(item['id']), source_name=company,
                   source_type='employer', source_url=item['hostedUrl'], employer_application_url=item['hostedUrl'],
                   source_checked_at=datetime.now(timezone.utc).isoformat())
    location = item.get('location') or 'unknown'
    return Job(title=item['title'], company=company, location=location,
               remote=remote_status(location, item.get('workplaceType') or item.get('isRemote')),
               description=item.get('descriptionHtml') or item.get('descriptionPlain') or '',
               employment_type=item.get('employmentType') or 'unknown',
               salary=json.dumps(item['compensation']) if item.get('compensation') else None,
               published=item.get('publishedAt'), url=item['jobUrl'], platform=platform, source_id=str(item.get('id') or item['jobUrl']),
               source_name=company, source_type='employer', source_url=item['jobUrl'], employer_application_url=item['jobUrl'],
               source_checked_at=datetime.now(timezone.utc).isoformat())


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



def _iso(value):
    if value in (None, ''):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _restriction_names(values):
    result = []
    for value in values or []:
        if isinstance(value, dict):
            name = value.get('name') or value.get('slug') or value.get('alpha2')
        else:
            name = value
        if name:
            result.append(str(name))
    return list(dict.fromkeys(result))


def normalize_public(source, item):
    """Normalize only fields supplied by the public provider; never synthesize employer facts."""
    source_id = source['id']
    source_name = source['name']
    checked = datetime.now(timezone.utc).isoformat()
    if source_id == 'arbeitnow':
        source_url = item.get('url')
        job_id = item.get('slug')
        title, company = item.get('title'), item.get('company_name')
        location = item.get('location') or 'Unknown'
        remote = remote_status(location, item.get('remote'))
        desc = item.get('description') or ''
        published = _iso(item.get('created_at'))
        restrictions = []
        application_url = None
        expiry = _iso(item.get('expires_at'))
        employment = ', '.join(item.get('job_types') or []) or 'Unknown'
        salary = None
        requirements = 'Unknown'
    elif source_id == 'remoteok':
        source_url = item.get('url')
        application_url = item.get('apply_url')
        job_id = item.get('id') or source_url
        title, company = item.get('position'), item.get('company')
        location = item.get('location') or 'Remote; hiring locations unspecified'
        remote = remote_status(location, True)
        desc = item.get('description') or ''
        published = _iso(item.get('date'))
        restrictions = _restriction_names(item.get('locations') or item.get('location_restrictions'))
        expiry = _iso(item.get('expires_at') or item.get('expiration_date'))
        employment = item.get('employment_type') or 'Unknown'
        salary = item.get('salary') or None
        requirements = 'Unknown'
    elif source_id == 'himalayas':
        source_url = item.get('applicationLink')
        # Provider terms require Apply to use applicationLink and retain attribution.
        application_url = None
        job_id = item.get('guid') or source_url
        title, company = item.get('title'), item.get('companyName')
        restrictions = _restriction_names(item.get('locationRestrictions'))
        if not restrictions and isinstance(item.get('locationRestrictions'), list):
            restrictions = ['Worldwide']  # API documents empty restrictions as worldwide.
        location = ', '.join(restrictions) if restrictions else 'Remote; hiring locations unspecified'
        remote = 'Remote'
        desc = item.get('description') or item.get('excerpt') or ''
        published = _iso(item.get('pubDate'))
        expiry = _iso(item.get('expiryDate'))
        employment = item.get('employmentType') or 'Unknown'
        salary = None
        if item.get('minSalary') is not None or item.get('maxSalary') is not None:
            salary = f"{item.get('currency') or ''} {item.get('minSalary') or ''}-{item.get('maxSalary') or ''} / {item.get('salaryPeriod') or 'annual'}".strip()
        requirements = 'Unknown'
    else:
        raise ValueError('Unsupported public source')
    if not source_url or not job_id or not title or not company:
        raise ValueError('Public posting is missing its provider URL, ID, title or employer')
    return Job(title=str(title), company=str(company), location=str(location), remote=remote,
               description=str(desc), requirements=requirements, employment_type=str(employment), salary=salary,
               published=published, url=application_url or source_url, platform=source_id, source_id=str(job_id),
               source_name=source_name, source_type='aggregator', source_url=source_url,
               employer_application_url=application_url, source_evidence_url=source.get('evidence_url'),
               source_checked_at=checked, expires_at=expiry, remote_permitted_locations=restrictions)


def _api_json(client, url, interval, params=None, robots_checked=False):
    if not robots_checked:
        check_robots(client, url, interval)
    response = client.get(url, params=params, follow_redirects=False)
    if response.is_redirect:
        target = urljoin(str(response.url), response.headers.get('location', ''))
        if urlsplit(target).scheme != 'https' or urlsplit(target).netloc != urlsplit(url).netloc:
            raise RuntimeError('Public feed redirect must remain HTTPS on the verified source host')
        check_robots(client, target, interval)
        response = client.get(target, follow_redirects=False)
    if response.status_code == 429:
        raise PublicFeedRateLimited('Public feed returned HTTP 429; stopped without retry')
    response.raise_for_status()
    return response.json(), response.status_code


def collect_public_source(source, client, interval=1, max_pages=500):
    """Fetch a documented public feed, respecting robots, provider pagination and conservative limits."""
    source_id = source['id']
    if source_id not in PUBLIC_FEEDS or source.get('status') != 'integrated':
        raise ValueError('Source is not an enabled documented public API')
    delay = max(float(interval), 2.0 if source_id == 'himalayas' else 1.0)
    result = CollectedJobs()
    seen = set()
    url = PUBLIC_FEEDS[source_id]
    check_robots(client, url, delay)
    checked_hosts = {urlsplit(url).hostname}
    cursor = None
    page = 1
    while True:
        if source_id == 'himalayas':
            params = {'limit': 20}
            if cursor:
                params['cursor'] = cursor
            if urlsplit(url).hostname not in checked_hosts:
                check_robots(client, url, delay)
                checked_hosts.add(urlsplit(url).hostname)
            payload, status = _api_json(client, url, delay, params=params, robots_checked=True)
            items = payload.get('jobs') if isinstance(payload, dict) else None
        elif source_id == 'arbeitnow':
            query = {'page': page}
            if urlsplit(url).hostname not in checked_hosts:
                check_robots(client, url, delay)
                checked_hosts.add(urlsplit(url).hostname)
            payload, status = _api_json(client, url, delay, params=query, robots_checked=True)
            items = payload.get('data') if isinstance(payload, dict) else None
        else:
            payload, status = _api_json(client, url, delay, robots_checked=True)
            items = [x for x in payload if isinstance(x, dict) and x.get('id') and x.get('position')] if isinstance(payload, list) else None
        if not isinstance(items, list):
            raise ValueError('Public feed response did not contain a job list')
        result.http_status = status
        result.pages_fetched += 1
        result.published_count += len(items)
        for index, item in enumerate(items):
            try:
                job = normalize_public(source, item)
                if job.source_id in seen:
                    continue
                seen.add(job.source_id)
                if job.expires_at:
                    try:
                        expiry = datetime.fromisoformat(job.expires_at.replace('Z', '+00:00'))
                        if expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=timezone.utc)
                        if expiry <= datetime.now(timezone.utc):
                            result.expired_count += 1
                            result.expired_postings.append(job)
                            continue
                    except ValueError:
                        pass
                result.append(job)
            except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
                message = f'{source_id}: malformed posting at index {index} on page {page}; skipped'
                result.warnings.append(message)
                log.warning(message)
        if source_id == 'remoteok':
            break
        if source_id == 'arbeitnow':
            links = payload.get('links') or {}
            next_url = links.get('next') if isinstance(links, dict) else None
            if not next_url:
                break
            parsed = urlsplit(next_url)
            allowed_hosts = {'www.arbeitnow.com', 'www.arbeitnow.nl', 'www.arbeitnow.co.uk'}
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if (parsed.scheme != 'https' or parsed.hostname not in allowed_hosts
                    or parsed.path != '/api/job-board-api' or set(query) != {'page'}
                    or not query['page'].isdigit()):
                raise RuntimeError('Arbeitnow pagination link escaped its documented regional API endpoints')
            page += 1
            if page > max_pages:
                result.pagination_complete = False
                break
            url = next_url
        else:
            cursor = payload.get('nextCursor')
            if not cursor:
                break
            if cursor in seen:
                raise RuntimeError('Himalayas returned a repeated pagination cursor')
            seen.add(cursor)
            if page >= max_pages:
                result.pagination_complete = False
                break
            page += 1
        time.sleep(delay)
    return result
