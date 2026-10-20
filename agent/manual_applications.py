"""Manual vacancy import and candidate-controlled application tracking.

This module never submits applications and never opens LinkedIn. URL import is
read-only, single-request-per-page, robots-aware and limited to public job pages.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from html import unescape
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select

from agent.db import Company, JobObservation, JobRow, ManualApplicationRecord, ManualVacancyLead, Source
from agent.models import Job, now

MANUAL_STATUSES = ('New', 'Interested', 'Applied', 'Interview', 'Rejected', 'Offer', 'Closed')
LEAD_EVIDENCE_STATUSES = (
    'Candidate-entered / unverified',
    'Third-party listing verified',
    'Official employer verified',
)
APPLIED_SOURCE = 'Candidate Confirmed'
TRACKER_SOURCE = 'Manual Tracker'
SUPPORTED_HOSTS = {
    'rabota.md': 'Rabota.md', 'delucru.md': 'Delucru.md', 'joblist.md': 'Joblist.md',
    'ejobs.ro': 'eJobs', 'bestjobs.eu': 'BestJobs', 'bestjobs.ro': 'BestJobs',
    'hipo.ro': 'Hipo', 'devjob.ro': 'DevJob.ro', 'dou.ua': 'DOU', 'djinni.co': 'Djinni',
    'remoteok.com': 'RemoteOK', 'weworkremotely.com': 'We Work Remotely',
    'himalayas.app': 'Himalayas', 'remotive.com': 'Remotive',
}
_TRACKING_QUERY = re.compile(r'^(utm_.+|gh_src)$', re.I)
_SOFTWARE_QA = re.compile(r'\b(qa|quality assurance|software quality engineer|software test(?:er|ing)?|test engineer|sdet|test automation|payments? testing|banking testing)\b', re.I)
_FINTECH_TECH = re.compile(r'\b(fintech|payments?|banking|financial technology)\b', re.I)
_TECHNICAL_FINTECH = re.compile(r'\b(software|api|integration|technical|platform|developer|engineer|automation|testing)\b', re.I)
_INDUSTRIAL = re.compile(r'\b(supplier|manufacturing|quality inspector|industrial|production quality)\b', re.I)


class ImportRejected(ValueError):
    """A public URL did not meet safe individual-vacancy import requirements."""


def normalize_job_url(value: str) -> str:
    """Normalize only URL mechanics for duplicate detection; retain source URL elsewhere."""
    parts = urlsplit((value or '').strip())
    if parts.scheme.lower() != 'https' or not parts.hostname or parts.username or parts.password:
        raise ImportRejected('Use a public HTTPS link to one vacancy.')
    if parts.port not in (None, 443):
        raise ImportRejected('Custom HTTPS ports are not supported for vacancy import.')
    host = parts.hostname.encode('idna').decode('ascii').lower()
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        raise ImportRejected('LinkedIn is excluded from vacancy import.')
    if host == 'jobs.md' or host.endswith('.jobs.md'):
        raise ImportRejected('Jobs.md has not been verified as an individual vacancy source yet.')
    if any(re.search(r'(token|secret|session|auth|password|api[_-]?key)', key, re.I)
           for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        raise ImportRejected('URLs containing credential-like query parameters cannot be stored.')
    path = re.sub(r'/+', '/', parts.path or '/')
    if path != '/':
        path = path.rstrip('/')
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not _TRACKING_QUERY.match(k)))
    return urlunsplit(('https', host, path, query, ''))


def parse_bulk_urls(text: str) -> list[str]:
    """Read one URL per line; reject search pages and arbitrary pasted prose later."""
    urls = []
    for line in (text or '').splitlines():
        item = line.strip().strip('"\'<>')
        if not item or item.startswith('#'):
            continue
        if not item.lower().startswith('https://'):
            raise ImportRejected('Bulk import accepts one HTTPS vacancy URL per line.')
        normalized = normalize_job_url(item)
        if normalized not in {normalize_job_url(x) for x in urls}:
            urls.append(item)
    return urls


def _host_allowed(host: str) -> bool:
    host = host.lower().rstrip('.')
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        return False
    if host == 'jobs.md' or host.endswith('.jobs.md'):
        return False
    if host.endswith('.greenhouse.io') or host == 'greenhouse.io':
        return True
    if host.endswith('.lever.co') or host == 'lever.co':
        return True
    if host.endswith('.workable.com') or host == 'workable.com':
        return True
    if host.endswith('.myworkdayjobs.com'):
        return True
    # Other public employer career pages are allowed only if their page contains
    # individual JobPosting structured data and is permitted by robots.txt.
    return bool(host)


def _public_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            return False
        return bool(answers) and all(ipaddress.ip_address(item[4][0]).is_global for item in answers)


def _robots_allows(client: httpx.Client, url: str) -> bool:
    parts = urlsplit(url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, '/robots.txt', '', ''))
    try:
        response = client.get(robots_url, follow_redirects=False)
    except httpx.HTTPError:
        return False
    if response.status_code in (404, 410):
        return True
    if response.status_code != 200:
        return False
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(response.text.splitlines())
    return parser.can_fetch('AIJobApplicationAgent/1.0', url)


def _job_posting_jsonld(html: str) -> dict | None:
    scripts = re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>',
                         html, re.I | re.S)
    def collect(value, found):
        if isinstance(value, dict):
            typ = value.get('@type')
            if typ == 'JobPosting' or (isinstance(typ, list) and 'JobPosting' in typ):
                found.append(value)
            for child in value.values():
                collect(child, found)
        elif isinstance(value, list):
            for child in value:
                collect(child, found)
    postings = []
    for script in scripts:
        try:
            collect(json.loads(unescape(script)), postings)
        except (ValueError, TypeError):
            continue
    return postings[0] if len(postings) == 1 else None


def _posting_location(posting: dict) -> tuple[str, str]:
    if str(posting.get('jobLocationType', '')).upper() == 'TELECOMMUTE':
        restrictions = posting.get('applicantLocationRequirements') or posting.get('jobLocation') or []
        if isinstance(restrictions, dict):
            restrictions = [restrictions]
        countries = []
        for entry in restrictions if isinstance(restrictions, list) else []:
            address = entry.get('address', entry) if isinstance(entry, dict) else {}
            if isinstance(address, dict) and address.get('addressCountry'):
                countries.append(str(address['addressCountry']))
        return ('; '.join(dict.fromkeys(countries)) or 'Unknown', 'Remote')
    locations = posting.get('jobLocation') or []
    if isinstance(locations, dict):
        locations = [locations]
    found = []
    for item in locations if isinstance(locations, list) else []:
        address = item.get('address', {}) if isinstance(item, dict) else {}
        bits = [address.get(k) for k in ('addressLocality', 'addressRegion', 'addressCountry') if isinstance(address, dict) and address.get(k)]
        if bits:
            found.append(', '.join(map(str, bits)))
    remote = 'Hybrid' if posting.get('jobLocationType') == 'HYBRID' else ('Unknown' if not found else 'Onsite')
    return ('; '.join(dict.fromkeys(found)) or 'Unknown', remote)


def _source_name(host: str) -> str:
    host = host.lower().removeprefix('www.')
    for domain, name in SUPPORTED_HOSTS.items():
        if host == domain or host.endswith('.' + domain):
            return name
    if 'greenhouse.io' in host:
        return 'Greenhouse'
    if 'lever.co' in host:
        return 'Lever'
    if 'workable.com' in host:
        return 'Workable'
    if 'myworkdayjobs.com' in host:
        return 'Workday'
    return 'Official employer career page'


def fetch_vacancy(url: str, *, client: httpx.Client | None = None) -> dict:
    """Fetch a single public job page after host and robots checks; do not retry or submit."""
    normalized = normalize_job_url(url)
    path = urlsplit(normalized).path.casefold().rstrip('/')
    query = dict(parse_qsl(urlsplit(normalized).query, keep_blank_values=True))
    if (re.search(r'/(search|results|vacancies|jobs|careers)$', path)
            or any(key.casefold() in {'q', 'query', 'search', 'page'} for key in query)):
        raise ImportRejected('Search and vacancy-listing pages are not individual job postings.')
    host = urlsplit(normalized).hostname or ''
    if not _host_allowed(host):
        raise ImportRejected('This source is not currently supported or verified for import.')
    if not _public_host(host):
        raise ImportRejected('The vacancy URL did not resolve to a public host.')
    owned = client is None
    client = client or httpx.Client(timeout=15, headers={'User-Agent': 'AIJobApplicationAgent/1.0 (+manual public vacancy import)'})
    try:
        if not _robots_allows(client, normalized):
            raise ImportRejected('robots.txt or source availability prevents importing this page.')
        try:
            response = client.get(normalized, follow_redirects=False)
        except httpx.HTTPError:
            raise ImportRejected('The public vacancy page could not be retrieved.') from None
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get('location')
            if not location:
                raise ImportRejected('The vacancy page redirected without a verifiable destination.')
            raise ImportRejected('The page redirected; open and import its final official vacancy URL directly.')
        if response.status_code != 200:
            raise ImportRejected(f'The vacancy source returned HTTP {response.status_code}; no retry was made.')
        if 'text/html' not in response.headers.get('content-type', '').lower():
            raise ImportRejected('The URL did not return an HTML vacancy page.')
        page = _job_posting_jsonld(response.text)
        if not page:
            raise ImportRejected('No individual JobPosting evidence was found; search or listing pages are not imported.')
        org = page.get('hiringOrganization') or {}
        company = org.get('name') if isinstance(org, dict) else None
        title = str(page.get('title') or '').strip()
        if not title or not company:
            raise ImportRejected('The official posting did not identify both a job title and employer.')
        location, remote = _posting_location(page)
        description = re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', str(page.get('description') or 'Unknown'))).strip()
        return {
            'title': title, 'company': str(company).strip(), 'location': location,
            'remote': remote, 'description': description or 'Unknown',
            'requirements': 'Unknown', 'employment_type': str(page.get('employmentType') or 'Unknown'),
            'salary': None, 'published': page.get('datePosted'), 'url': url.strip(),
            'source_name': _source_name(host), 'platform': _source_name(host),
            'source_id': f'manual:{host}', 'source_type': 'manual-import',
            'source_url': url.strip(),
            'remote_permitted_locations': [], 'work_authorization_requirements': 'Unknown',
            'relocation_requirement': 'Unknown', 'visa_sponsorship': 'Unknown',
            'contractor_eligibility': 'Unknown', 'employer_of_record_availability': 'Unknown',
        }
    finally:
        if owned:
            client.close()


def _existing_job(session, normalized: str):
    for row in session.scalars(select(JobRow)):
        if row.canonical_url:
            try:
                if normalize_job_url(row.canonical_url) == normalized:
                    return row
            except ImportRejected:
                pass
        try:
            data = json.loads(row.payload or '{}')
            if normalize_job_url(str(data.get('url', ''))) == normalized:
                return row
        except (ValueError, ImportRejected):
            pass
    for observation in session.scalars(select(JobObservation)):
        try:
            if normalize_job_url(observation.canonical_url or '') == normalized:
                return session.get(JobRow, observation.job_id)
            data = json.loads(observation.payload or '{}')
            if normalize_job_url(str(data.get('url', ''))) == normalized:
                return session.get(JobRow, observation.job_id)
        except (ValueError, ImportRejected):
            continue
    return None


def is_relevant_vacancy(title: str, description: str) -> bool:
    text = f'{title} {description}'
    if _INDUSTRIAL.search(text) and not re.search(r'\b(software|web|api|mobile|test automation|automation framework)\b', text, re.I):
        return False
    return bool(_SOFTWARE_QA.search(title) or (_SOFTWARE_QA.search(text) and _FINTECH_TECH.search(text))
                or (_FINTECH_TECH.search(text) and _TECHNICAL_FINTECH.search(title)))


def import_vacancy(session, url: str, *, posting: dict | None = None) -> dict:
    """Import one verified individual listing and initialize a manual status row."""
    normalized = normalize_job_url(url)
    existing = _existing_job(session, normalized)
    if existing:
        return {'status': 'duplicate', 'job_id': existing.id, 'title': json.loads(existing.payload).get('title', 'Unknown')}
    posting = posting or fetch_vacancy(url)
    if not is_relevant_vacancy(posting.get('title', ''), posting.get('description', '')):
        raise ImportRejected('This posting is not clearly Software QA or adjacent technical fintech work.')
    company = session.scalar(select(Company).where(Company.name == posting['company']))
    if company is None:
        company = Company(name=posting['company'])
        session.add(company)
        session.flush()
    source = session.scalar(select(Source).where(Source.platform == posting['platform'], Source.board == posting['source_id']))
    if source is None:
        source = Source(company_id=company.id, platform=posting['platform'], board=posting['source_id'])
        session.add(source)
        session.flush()
    posting = dict(posting)
    posting['source_id'] = str(source.id)
    job = Job.model_validate(posting)
    identity = f'manual:{normalize_job_url(url)}'
    row = JobRow(source_id=source.id, identity=identity, canonical_url=normalized,
                 fingerprint=identity, payload=job.model_dump_json(),
                 first_discovered=now(), last_checked=now(), status='New')
    session.add(row)
    session.flush()
    session.add(ManualApplicationRecord(job_id=row.id, status='New', source=TRACKER_SOURCE))
    session.flush()
    return {'status': 'imported', 'job_id': row.id, 'title': job.title}


def add_manual_vacancy(session, *, employer: str, title: str, url: str,
                       country: str = 'Unknown', city: str = 'Unknown',
                       work_arrangement: str = 'Unknown', source: str = 'Unknown',
                       notes: str = '', evidence_status: str = 'Candidate-entered / unverified',
                       confirmed_active: bool = False, confirmed_official: bool = False) -> dict:
    """Add a manually described vacancy without pretending absent metadata was retrieved.

    The default record is explicitly unverified. Higher evidence labels require the
    candidate to affirm that the individual listing is active, and that an official
    employer page was checked for the strongest label.
    """
    values = {name: (value or '').strip() for name, value in {
        'employer': employer, 'title': title, 'url': url, 'country': country,
        'city': city, 'work_arrangement': work_arrangement, 'source': source,
        'notes': notes,
    }.items()}
    if not values['employer'] or not values['title'] or not values['url']:
        raise ImportRejected('Employer, job title, and an individual vacancy URL are required.')
    if evidence_status not in LEAD_EVIDENCE_STATUSES:
        raise ImportRejected('Choose one of the supported evidence labels.')
    if evidence_status != 'Candidate-entered / unverified' and not confirmed_active:
        raise ImportRejected('Confirm that this individual vacancy page is currently active before marking it verified.')
    if evidence_status == 'Official employer verified' and not confirmed_official:
        raise ImportRejected('Confirm that the active vacancy was checked on the employer’s official career site.')

    normalized = normalize_job_url(values['url'])
    existing = _existing_job(session, normalized)
    if existing:
        return {'status': 'duplicate', 'job_id': existing.id, 'title': json.loads(existing.payload).get('title', 'Unknown')}
    host = urlsplit(normalized).hostname or 'unknown'
    company = session.scalar(select(Company).where(Company.name == values['employer']))
    if company is None:
        company = Company(name=values['employer'])
        session.add(company)
        session.flush()
    source_key = f'manual-entry:{host}'
    source_row = session.scalar(select(Source).where(Source.platform == 'Manual entry', Source.board == source_key))
    if source_row is None:
        source_row = Source(company_id=company.id, platform='Manual entry', board=source_key)
        session.add(source_row)
        session.flush()

    location_parts = [item for item in (values['city'], values['country']) if item.casefold() != 'unknown']
    location = ', '.join(location_parts) or 'Unknown'
    job = Job(
        title=values['title'], company=values['employer'], location=location,
        remote=values['work_arrangement'], description='Unknown', requirements='Unknown',
        employment_type='Unknown', salary=None, published=None, url=values['url'],
        platform='Manual entry', source_id=str(source_row.id), source_name=values['source'] or 'Unknown',
        source_type='manual-import', source_url=values['url'],
        remote_permitted_locations=[], work_authorization_requirements='Unknown',
        relocation_requirement='Unknown', visa_sponsorship='Unknown',
        contractor_eligibility='Unknown', employer_of_record_availability='Unknown',
    )
    identity = f'manual-lead:{normalized}'
    row = JobRow(source_id=source_row.id, identity=identity, canonical_url=normalized,
                 fingerprint=identity, payload=job.model_dump_json(),
                 first_discovered=now(), last_checked=now(), status='New')
    session.add(row)
    session.flush()
    session.add(ManualApplicationRecord(job_id=row.id, status='New', source=TRACKER_SOURCE))
    session.add(ManualVacancyLead(
        job_id=row.id, evidence_status=evidence_status, country=values['country'] or 'Unknown',
        city=values['city'] or 'Unknown', listing_notes=values['notes'],
    ))
    session.flush()
    return {'status': 'imported', 'job_id': row.id, 'title': job.title, 'evidence_status': evidence_status}


def set_manual_application(session, job_id: int, status: str, *, applied_date: str | None = None,
                           notes: str | None = None) -> ManualApplicationRecord:
    """Update candidate-owned application status by stable vacancy ID only."""
    if status not in MANUAL_STATUSES:
        raise ValueError('Unsupported manual application status.')
    if applied_date:
        date.fromisoformat(applied_date)
    row = session.scalar(select(ManualApplicationRecord).where(ManualApplicationRecord.job_id == job_id))
    if row is None:
        if session.get(JobRow, job_id) is None:
            raise ValueError('Vacancy does not exist.')
        row = ManualApplicationRecord(job_id=job_id, status='New', source=TRACKER_SOURCE)
        session.add(row)
    row.status = status
    if status == 'Applied' and not (applied_date or row.applied_date):
        applied_date = date.today().isoformat()
    if applied_date is not None:
        row.applied_date = applied_date
    if notes is not None:
        row.notes = notes
    row.updated = datetime.now(timezone.utc).isoformat()
    session.flush()
    return row


def confirm_manual_applications(session, job_ids, *, applied_date: str) -> int:
    """Record candidate-confirmed manual submissions without touching jobs.status or agent records."""
    date.fromisoformat(applied_date)
    updated = 0
    for job_id in job_ids:
        if session.get(JobRow, job_id) is None:
            raise ValueError(f'Vacancy ID {job_id} does not exist.')
        row = session.scalar(select(ManualApplicationRecord).where(ManualApplicationRecord.job_id == job_id))
        if row is None:
            row = ManualApplicationRecord(job_id=job_id)
            session.add(row)
        row.status = 'Applied'
        row.source = APPLIED_SOURCE
        row.applied_date = applied_date
        row.notes = 'Manually submitted; confirmed by candidate.'
        row.updated = now()
        updated += 1
    session.flush()
    return updated


def record_for_job(session, job_id: int) -> ManualApplicationRecord | None:
    return session.scalar(select(ManualApplicationRecord).where(ManualApplicationRecord.job_id == job_id))


def priority_group(job: Job) -> int:
    """A transparent geographic tier; professional score remains an independent sort factor."""
    loc = (job.location or '').casefold()
    remote = job.remote == 'Remote'
    allowed = ' '.join(job.remote_permitted_locations).casefold()
    if remote and any(x in allowed for x in ('moldova', 'moldavia', 'chisinau', 'chișinău')):
        return 0
    if remote and any(x in loc for x in ('moldova', 'moldavia', 'chisinau', 'chișinău')):
        return 1
    if any(x in loc for x in ('moldova', 'moldavia', 'chisinau', 'chișinău')):
        return 2
    if any(x in loc for x in ('romania', 'românia', 'bucharest', 'bucurești', 'bucharest')):
        return 3
    if any(x in loc for x in ('eu', 'european union', 'europe')):
        return 4
    if any(x in loc for x in ('ukraine', 'україна')):
        return 5
    if any(x in loc for x in ('united kingdom', ' uk', 'worldwide', 'global', 'anywhere', 'international')):
        return 6
    return 7
