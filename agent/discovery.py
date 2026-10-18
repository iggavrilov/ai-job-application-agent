import json
import logging
import hashlib
import httpx
from filelock import FileLock
from sqlalchemy import select, or_
from agent.settings import ROOT, profile, rules
from agent.registry import load_boards
from agent.models import now
from agent.db import database, CandidateProfile, Company, Source, JobRow, JobObservation, MatchRow, SearchRun
from agent.collectors import collect
from agent.matching import relevant, canonical, fingerprint, evaluate
from agent.logging_config import setup_logging
log = logging.getLogger(__name__)


def upsert_job(session, job, source):
    identity = f'{job.platform}:{source.board}:{job.source_id}'
    observation = session.scalar(select(JobObservation).where(JobObservation.identity==identity))
    # Exact source identity wins over fuzzy keys, including legacy pre-observation rows.
    row = session.get(JobRow, observation.job_id) if observation else session.scalar(select(JobRow).where(JobRow.identity==identity))
    if row is None:
        alias = session.scalar(select(JobObservation).where(JobObservation.canonical_url==canonical(job.url)).order_by(JobObservation.id))
        if alias:
            row = session.get(JobRow,alias.job_id)
    if row is None:
        row = session.scalar(select(JobRow).where(or_(JobRow.canonical_url==canonical(job.url),JobRow.fingerprint==fingerprint(job))).order_by(JobRow.id))
    is_new = row is None
    if row is None:
        row = JobRow(source_id=source.id,identity=identity,canonical_url=canonical(job.url),fingerprint=fingerprint(job),payload=job.model_dump_json())
        session.add(row)
        session.flush()
    # Discovery date in the normalized payload must agree with persistent first discovery.
    job = job.model_copy(update={'discovered':row.first_discovered})
    row.payload = job.model_dump_json()
    row.last_checked = now()
    if observation is None:
        observation = JobObservation(job_id=row.id,source_id=source.id,identity=identity,canonical_url=canonical(job.url),payload=job.model_dump_json())
        session.add(observation)
    observation.payload = job.model_dump_json()
    observation.canonical_url = canonical(job.url)
    observation.last_checked = now()
    session.flush()
    return row, is_new


def discover(use_ai=True):
    setup_logging()
    (ROOT/'data').mkdir(exist_ok=True)
    with FileLock(str(ROOT/'data/search.lock'), timeout=0):
        factory = database()
        with factory() as session:
            # Record configuration/profile errors as failed runs too.
            run = SearchRun()
            session.add(run)
            session.commit()
            run_id = run.id
            errors, successes, fetched, added = [], 0, 0, 0
            try:
                candidate, config = profile(), rules()
                boards, registry_errors = load_boards()
                errors.extend(registry_errors)
                payload = candidate.model_dump_json()
                cp = session.scalar(select(CandidateProfile).where(CandidateProfile.payload==payload))
                if cp is None:
                    cp = CandidateProfile(payload=payload)
                    session.add(cp)
                session.commit()
                candidate_id = cp.id
                enabled = [b for b in boards if b['enabled']]
                if not enabled:
                    errors.append('No valid enabled boards configured')
                with httpx.Client(timeout=30,headers={'User-Agent':'QAJobAgent/0.1 (public API reader)'},follow_redirects=True) as client:
                    for board in enabled:
                        try:
                            jobs = collect(board,client,config.get('request_interval_seconds',1))
                            warnings = getattr(jobs,'warnings',[])
                            new_count = 0
                            company = session.scalar(select(Company).where(Company.name==board['company']))
                            if company is None:
                                company = Company(name=board['company'])
                                session.add(company)
                                session.flush()
                            source = session.scalar(select(Source).where(Source.platform==board['platform'],Source.board==board['board']))
                            if source is None:
                                source = Source(company_id=company.id,platform=board['platform'],board=board['board'])
                                session.add(source)
                                session.flush()
                            for job in jobs:
                                if not relevant(job,config):
                                    continue
                                row, is_new = upsert_job(session,job,source)
                                new_count += int(is_new)
                                result = evaluate(job,candidate,config,use_ai)
                                version = result.version+':'+hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:10]
                                # Context is stored with each historical match, not just latest job text.
                                context = result.model_dump() | {'job_snapshot':job.model_dump(mode='json'),'search_rules':config}
                                session.add(MatchRow(job_id=row.id,candidate_id=candidate_id,payload=json.dumps(context),version=version))
                            session.commit()
                            # Count only committed work. Failure rolls back the whole board, then continues.
                            fetched += len(jobs)
                            added += new_count
                            errors.extend(warnings)
                            successes += 1
                            log.info('%s/%s: fetched %s, new relevant %s',board['platform'],board['board'],len(jobs),new_count)
                        except Exception as exc:
                            session.rollback()
                            # SQL/SDK exception bodies can include profile values; store safe diagnostics.
                            code = f' HTTP {exc.response.status_code}' if isinstance(exc,httpx.HTTPStatusError) else ''
                            message = f"{board['platform']}/{board['board']}: {type(exc).__name__}{code}; board rolled back, processing continues"
                            errors.append(message)
                            log.warning(message)
                status = 'Failed' if not successes else 'Partial' if errors else 'Success'
            except Exception as exc:
                session.rollback()
                errors.append(f'Search setup failed: {type(exc).__name__}; check local profile/configuration')
                status = 'Failed'
            run = session.get(SearchRun,run_id)
            run.discovered, run.new = fetched, added
            run.status, run.errors, run.finished = status,json.dumps(errors),now()
            session.commit()
            log.info('Search %s finished: %s; fetched=%s new=%s errors=%s',run_id,status,fetched,added,len(errors))
            return {'id':run_id,'status':status,'fetched':fetched,'new_relevant':added,'errors':errors}
