"""Employer ownership evidence must precede activation; no guessed board slugs."""
import html
import re
from urllib.parse import urlsplit, parse_qs, urljoin
from html.parser import HTMLParser
from agent.collectors import check_robots, collect
from agent.models import now


def board_links(content):
    """Extract exact observed ATS identifiers from public employer HTML/embedded URLs."""
    text = html.unescape(content).replace('\\/', '/').replace('\\u002F', '/')
    found = set()
    for url in re.findall(r'(?:https:)?//[^\s<>"\x27\\]+', text):
        if url.startswith('//'):
            url = 'https:' + url
        parsed = urlsplit(url)
        host = parsed.hostname
        parts = parsed.path.strip('/').split('/')
        if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io', 'job-boards.eu.greenhouse.io'):
            platform, board = 'greenhouse', parts[0]
            if board == 'embed':
                board = parse_qs(parsed.query).get('for', [''])[0]
        elif host == 'jobs.lever.co':
            platform, board = 'lever', parts[0]
        elif host == 'jobs.ashbyhq.com':
            platform, board = 'ashby', parts[0]
        elif host == 'boards-api.greenhouse.io' and len(parts) >= 4 and parts[:2] == ['v1', 'boards']:
            platform, board = 'greenhouse', parts[2]
        elif host == 'my.greenhouse.io' and 'job_board' in parse_qs(parsed.query):
            # Public employer job-alert links expose the exact board token.
            # Extract it locally; never visit the login URL or create an alert.
            platform, board = 'greenhouse', parse_qs(parsed.query)['job_board'][0]
        else:
            continue
        if re.fullmatch(r'[A-Za-z0-9_-]+', board):
            found.add((platform, board))
    return sorted(found)


def employer_page(client, url):
    """Check each redirect before fetching it; an ATS redirect itself is link evidence."""
    for _ in range(6):
        check_robots(client, url)
        response = client.get(url, follow_redirects=False)
        if response.is_redirect:
            target = urljoin(url, response.headers['location'])
            if urlsplit(target).scheme != 'https':
                raise ValueError('Only HTTPS employer redirects are supported')
            if board_links(target):
                return target, target
            url = target
            continue
        response.raise_for_status()
        return str(response.url), response.text
    raise ValueError('Too many employer redirects')


class CareersLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(value for key, value in attrs if key == 'href' and value)


def careers_sublinks(url, content):
    parser = CareersLinks()
    parser.feed(content)
    host = urlsplit(url).hostname.removeprefix('www.')
    links = set()
    for href in parser.links:
        target = urljoin(url, href).split('#')[0]
        parsed = urlsplit(target)
        if parsed.scheme != 'https' or not parsed.hostname:
            continue
        if parsed.hostname != host and not parsed.hostname.endswith('.' + host):
            continue
        if re.search(r'(careers|jobs|open.positions|vacancies|openings)', parsed.path, re.I) and target != url:
            links.add(target)
    return sorted(links, key=lambda value:(len(value),value))[:4]


def verify_employer(company, careers_url, client):
    """Return evidence; callers choose which successfully verified boards to activate."""
    source = urlsplit(careers_url)
    if source.scheme != 'https' or not source.hostname or source.username or source.password:
        raise ValueError('Supply a public HTTPS official employer careers URL')
    if source.hostname in ('jobs.lever.co','jobs.ashbyhq.com','boards.greenhouse.io','job-boards.greenhouse.io','job-boards.eu.greenhouse.io','boards-api.greenhouse.io','my.greenhouse.io'):
        raise ValueError('An ATS URL alone is not official employer ownership evidence')
    final_url, content = employer_page(client, careers_url)
    observed = {link: final_url for link in board_links(content)}
    if not observed:
        for sublink in careers_sublinks(final_url, content):
            try:
                target, body = employer_page(client, sublink)
                observed.update({link: target for link in board_links(body)})
            except Exception:
                continue  # No ownership claim from an inaccessible page.
    results = []
    for (platform, board), evidence_url in sorted(observed.items()):
        entry = dict(company=company, platform=platform, board=board, enabled=False,
                     verification_status='pending', evidence_url=careers_url)
        evidence = dict(entry, checked_at=now(), observed_on=evidence_url)
        try:
            jobs = collect(entry, client)
            evidence.update(status='verified', api_http_status=jobs.http_status,
                            published=jobs.published_count, normalized=len(jobs), warnings=jobs.warnings)
            entry.update(enabled=True, verification_status='verified', verified_at=evidence['checked_at'])
        except Exception as exc:
            evidence.update(status='unavailable', error=type(exc).__name__,
                            policy_http_status=getattr(exc,'policy_http_status',None))
        results.append((entry, evidence))
    return results
