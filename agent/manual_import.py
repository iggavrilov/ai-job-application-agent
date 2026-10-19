"""Offline import of user-provided LinkedIn job URLs or alert text. Never fetches LinkedIn."""
from __future__ import annotations
import re
from urllib.parse import urlparse, unquote
from sqlalchemy import select
from agent.db import ManualImportLead, JobRow
from agent.models import Job


def _label(text, names):
    pattern = r"(?im)^\s*(?:" + "|".join(re.escape(name) for name in names) + r")\s*[:—-]\s*(.+?)\s*$"
    found = re.search(pattern, text or "")
    return found.group(1).strip() if found else None


def parse_user_content(text):
    """Extract safe metadata from content explicitly pasted by the user; persist no raw text."""
    content = str(text or "")
    urls = []
    for raw in re.findall(r"https?://[^\s<>\"']+", content):
        value = raw.rstrip('.,);]>}')
        parsed = urlparse(value)
        host = (parsed.hostname or '').lower().removeprefix('www.')
        if parsed.scheme == 'https' and host == 'linkedin.com' and re.search(r"^/jobs/view/", parsed.path, re.I):
            canonical = f"https://www.linkedin.com{parsed.path.rstrip('/')}"
            if canonical not in urls:
                urls.append(canonical)
    leads = []
    title = _label(content, ['Title','Job title','Position'])
    company = _label(content, ['Company','Employer'])
    location = _label(content, ['Location','Job location']) or 'Unknown'
    apply_url = _label(content, ['Employer application URL','Application URL','Apply URL'])
    if apply_url:
        apply_url = apply_url.strip()
        parsed = urlparse(apply_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname.lower().endswith('linkedin.com'):
            apply_url = None
    for url in urls:
        path = urlparse(url).path
        slug = unquote(path.rsplit('/',1)[-1])
        slug_title = re.sub(r"-\d{5,}$", "", slug).replace('-', ' ').strip().title()
        leads.append({'linkedin_url':url, 'title':title or (slug_title if slug_title else 'Needs manual identification'),
                      'company':company or 'Needs manual identification', 'location':location,
                      'employer_application_url':apply_url, 'status':'Needs Official Verification'})
    return leads


def _find_official_match(session, lead):
    if lead['company'] == 'Needs manual identification' or lead['title'] == 'Needs manual identification':
        return None
    for row in session.scalars(select(JobRow)):
        try:
            job=Job.model_validate_json(row.payload)
        except Exception:
            continue
        if job.platform not in {'greenhouse','lever','ashby'}:
            continue
        if job.company.casefold().strip()==lead['company'].casefold().strip() and job.title.casefold().strip()==lead['title'].casefold().strip():
            return row.id
    return None


def import_user_content(session, text):
    """Idempotently upsert sanitized leads, preserving any prior manual reconciliation status."""
    leads=parse_user_content(text)
    saved=[]
    for lead in leads:
        existing=session.scalar(select(ManualImportLead).where(ManualImportLead.linkedin_url==lead['linkedin_url']))
        if existing is None:
            existing=ManualImportLead(**lead, matched_job_id=_find_official_match(session,lead))
            session.add(existing)
        else:
            existing.title=lead['title']
            existing.company=lead['company']
            existing.location=lead['location']
            existing.employer_application_url=lead['employer_application_url']
            existing.matched_job_id=_find_official_match(session,lead)
        saved.append(existing)
    session.flush()
    return saved


def reconcile_imported_leads(session):
    """Match sanitized imported leads against the current local, verified ATS inventory."""
    changed=0
    for lead in session.scalars(select(ManualImportLead).where(ManualImportLead.matched_job_id.is_(None))):
        match_id=_find_official_match(session,{'company':lead.company,'title':lead.title})
        if match_id:
            lead.matched_job_id=match_id
            changed+=1
    session.flush()
    return changed
