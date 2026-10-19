"""Documented public board APIs; no global search or authenticated access."""
import html
import json
import re
import time
import logging
from pathlib import Path
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
        self.checkpoint_path = None


def _write_public_checkpoint(path, source_id, result, next_state, complete):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'source_id': source_id,
        'next_state': next_state,
        'complete': bool(complete),
        'jobs': [job.model_dump(mode='json') for job in result],
        'expired_postings': [job.model_dump(mode='json') for job in result.expired_postings],
        'published_count': result.published_count,
        'pages_fetched': result.pages_fetched,
        'expired_count': result.expired_count,
    }
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    temporary.replace(path)


def clear_public_checkpoint(path):
    if path is not None:
        Path(path).unlink(missing_ok=True)
        Path(path).with_suffix(Path(path).suffix + '.rate-limit.json').unlink(missing_ok=True)

PUBLIC_FEEDS = {
    'arbeitnow': 'https://www.arbeitnow.com/api/job-board-api?page=1',
    'remoteok': 'https://remoteok.com/api',
    'himalayas': 'https://himalayas.app/jobs/api?limit=20',
}


class PublicFeedRateLimited(RuntimeError):
    """Safe marker for a provider rate limit after one bounded retry."""
    policy_http_status = 429

    def __init__(self, message, resume_state=None, retry_after_seconds=None):
        super().__init__(message)
        self.resume_state = resume_state
        self.retry_after_seconds = retry_after_seconds

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


def collect(board, client, interval=1, rate_limit_path=None):
    platform = board['platform']
    url = ENDPOINTS[platform].format(board=board['board'])
    source_id = f"{platform}:{board['board']}"
    _check_rate_limit_cooldown(rate_limit_path, source_id)
    check_robots(client, url, interval)
    response = _request_with_bounded_retry(client,url,interval,source_id,rate_limit_path)
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


def _parse_retry_after(value):
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (ValueError, AttributeError):
        try:
            from email.utils import parsedate_to_datetime
            seconds = (parsedate_to_datetime(value).astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds)


def _retry_after_seconds(value, maximum=10):
    seconds = _parse_retry_after(value)
    if seconds is None:
        return 1.0
    return seconds if seconds <= maximum else None


def _rate_limit_sidecar(path):
    return Path(path).with_suffix(Path(path).suffix + '.rate-limit.json') if path else None


def _check_rate_limit_cooldown(path, source_id):
    sidecar=_rate_limit_sidecar(path)
    if not sidecar or not sidecar.is_file():
        return
    cooldown=json.loads(sidecar.read_text(encoding='utf-8'))
    if cooldown.get('source_id')!=source_id:
        raise ValueError('Source rate-limit checkpoint identity mismatch')
    remaining=float(cooldown.get('blocked_until',0))-time.time()
    if remaining>0:
        raise PublicFeedRateLimited('Source cooldown is active; endpoint request skipped',retry_after_seconds=remaining)
    sidecar.unlink(missing_ok=True)


def _store_rate_limit_cooldown(path, source_id, seconds):
    sidecar=_rate_limit_sidecar(path)
    if not sidecar:
        return
    sidecar.parent.mkdir(parents=True,exist_ok=True)
    sidecar.write_text(json.dumps({'source_id':source_id,'blocked_until':time.time()+(seconds or 300)}),encoding='utf-8')


def _request_with_bounded_retry(client, url, interval, source_id, rate_limit_path=None, params=None):
    def request_once():
        target_url=url
        for _ in range(6):
            response=client.get(target_url,params=params,follow_redirects=False)
            if not response.is_redirect:
                return response
            target=urljoin(str(response.url),response.headers.get('location',''))
            if urlsplit(target).scheme!='https':
                raise RuntimeError('Public API redirect must use HTTPS')
            check_robots(client,target,interval)
            target_url=target
        raise RuntimeError('Too many public API redirects')

    response=request_once()
    if response.status_code==429:
        retry_after=_parse_retry_after(response.headers.get('Retry-After'))
        wait=_retry_after_seconds(response.headers.get('Retry-After'))
        retried=False
        if wait is not None:
            time.sleep(wait)
            retried=True
            response=request_once()
        if response.status_code==429:
            retry_after=_parse_retry_after(response.headers.get('Retry-After')) or retry_after
            cooldown=max(60.0,retry_after) if retry_after is not None else 300.0
            suffix=' after one bounded retry' if retried else ' and Retry-After exceeds the retry safety cap'
            _store_rate_limit_cooldown(rate_limit_path,source_id,cooldown)
            raise PublicFeedRateLimited('Source returned HTTP 429'+suffix,retry_after_seconds=cooldown)
    response.raise_for_status()
    return response


def _api_json(client, url, interval, params=None, robots_checked=False, resume_state=None):
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
        retry_after = _parse_retry_after(response.headers.get('Retry-After'))
        wait = _retry_after_seconds(response.headers.get('Retry-After'))
        retried = False
        if wait is not None:
            time.sleep(wait)
            retried = True
            response = client.get(url, params=params, follow_redirects=False)
            if response.is_redirect:
                target = urljoin(str(response.url), response.headers.get('location', ''))
                if urlsplit(target).scheme != 'https' or urlsplit(target).netloc != urlsplit(url).netloc:
                    raise RuntimeError('Public feed redirect must remain HTTPS on the verified source host')
                check_robots(client, target, interval)
                response = client.get(target, follow_redirects=False)
        if response.status_code == 429:
            suffix = ' after one bounded retry' if retried else ' and Retry-After exceeds the retry safety cap'
            retry_after = _parse_retry_after(response.headers.get('Retry-After')) or retry_after
            cooldown = max(60.0, retry_after) if retry_after is not None else 300.0
            raise PublicFeedRateLimited('Public feed returned HTTP 429' + suffix, resume_state, cooldown)
    response.raise_for_status()
    return response.json(), response.status_code


def collect_public_source(source, client, interval=1, max_pages=500, resume_state=None, checkpoint_path=None):
    """Fetch a documented public feed, respecting robots, provider pagination and conservative limits."""
    source_id = source['id']
    if source_id not in PUBLIC_FEEDS or source.get('status') != 'integrated':
        raise ValueError('Source is not an enabled documented public API')
    delay = max(float(interval), 2.0 if source_id == 'himalayas' else 1.0)
    result = CollectedJobs()
    seen = set()
    url = PUBLIC_FEEDS[source_id]
    rate_path = Path(checkpoint_path).with_suffix(Path(checkpoint_path).suffix + '.rate-limit.json') if checkpoint_path else None
    if rate_path and rate_path.is_file():
        cooldown = json.loads(rate_path.read_text(encoding='utf-8'))
        if cooldown.get('source_id') != source_id:
            raise ValueError('Public source rate-limit checkpoint identity mismatch')
        remaining = float(cooldown.get('blocked_until', 0)) - time.time()
        if remaining > 0:
            raise PublicFeedRateLimited('Public source cooldown is active; endpoint request skipped',
                                        retry_after_seconds=remaining)
        rate_path.unlink(missing_ok=True)

    def request_page(*args, **kwargs):
        try:
            return _api_json(*args, **kwargs)
        except PublicFeedRateLimited as exc:
            if rate_path:
                rate_path.parent.mkdir(parents=True, exist_ok=True)
                rate_path.write_text(json.dumps({'source_id':source_id,
                    'blocked_until':time.time() + (exc.retry_after_seconds or 300)}), encoding='utf-8')
            raise

    check_robots(client, url, delay)
    checked_hosts = {urlsplit(url).hostname}
    result.checkpoint_path = checkpoint_path
    if checkpoint_path and Path(checkpoint_path).is_file():
        saved = json.loads(Path(checkpoint_path).read_text(encoding='utf-8'))
        if saved.get('source_id') != source_id:
            raise ValueError('Public source checkpoint identity mismatch')
        result.extend(Job.model_validate(item) for item in saved.get('jobs', []))
        result.expired_postings = [Job.model_validate(item) for item in saved.get('expired_postings', [])]
        result.published_count = int(saved.get('published_count', 0))
        result.pages_fetched = int(saved.get('pages_fetched', 0))
        result.expired_count = int(saved.get('expired_count', 0))
        seen.update(job.source_id for job in result)
        cursor = (saved.get('next_state') or {}).get('cursor')
        page = int((saved.get('next_state') or {}).get('page', 1))
        if saved.get('complete'):
            result.pagination_complete = True
            return result
    else:
        cursor = (resume_state or {}).get('cursor')
        page = int((resume_state or {}).get('page', 1))
    pages_this_run = 0
    while True:
        if source_id == 'himalayas':
            params = {'limit': 20}
            if cursor:
                params['cursor'] = cursor
            if urlsplit(url).hostname not in checked_hosts:
                check_robots(client, url, delay)
                checked_hosts.add(urlsplit(url).hostname)
            payload, status = request_page(client, url, delay, params=params, robots_checked=True, resume_state={'cursor':cursor,'page':page})
            items = payload.get('jobs') if isinstance(payload, dict) else None
        elif source_id == 'arbeitnow':
            query = {'page': page}
            if urlsplit(url).hostname not in checked_hosts:
                check_robots(client, url, delay)
                checked_hosts.add(urlsplit(url).hostname)
            payload, status = request_page(client, url, delay, params=query, robots_checked=True, resume_state={'cursor':cursor,'page':page})
            items = payload.get('data') if isinstance(payload, dict) else None
        else:
            payload, status = request_page(client, url, delay, robots_checked=True, resume_state={'cursor':cursor,'page':page})
            items = [x for x in payload if isinstance(x, dict) and x.get('id') and x.get('position')] if isinstance(payload, list) else None
        if not isinstance(items, list):
            raise ValueError('Public feed response did not contain a job list')
        result.http_status = status
        result.pages_fetched += 1
        pages_this_run += 1
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
        next_state = None
        if source_id == 'remoteok':
            _write_public_checkpoint(checkpoint_path, source_id, result, None, True)
            break
        if source_id == 'arbeitnow':
            links = payload.get('links') or {}
            next_url = links.get('next') if isinstance(links, dict) else None
            if not next_url:
                _write_public_checkpoint(checkpoint_path, source_id, result, None, True)
                break
            parsed = urlsplit(next_url)
            allowed_hosts = {'www.arbeitnow.com', 'www.arbeitnow.nl', 'www.arbeitnow.co.uk'}
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if (parsed.scheme != 'https' or parsed.hostname not in allowed_hosts
                    or parsed.path != '/api/job-board-api' or set(query) != {'page'}
                    or not query['page'].isdigit()):
                raise RuntimeError('Arbeitnow pagination link escaped its documented regional API endpoints')
            page += 1
            if pages_this_run >= max_pages:
                result.pagination_complete = False
                _write_public_checkpoint(checkpoint_path, source_id, result, {'cursor':None,'page':page}, False)
                break
            url = next_url
            next_state = {'cursor': None, 'page': page}
        else:
            cursor = payload.get('nextCursor')
            if not cursor:
                _write_public_checkpoint(checkpoint_path, source_id, result, None, True)
                break
            if cursor in seen:
                raise RuntimeError('Himalayas returned a repeated pagination cursor')
            seen.add(cursor)
            if pages_this_run >= max_pages:
                result.pagination_complete = False
                _write_public_checkpoint(checkpoint_path, source_id, result, {'cursor':cursor, 'page':page+1}, False)
                break
            page += 1
            next_state = {'cursor': cursor, 'page': page}
        _write_public_checkpoint(checkpoint_path, source_id, result, next_state, False)
        time.sleep(delay)
    return result
