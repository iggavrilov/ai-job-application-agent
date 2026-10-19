import json
import logging
import hashlib
from collections import Counter
import httpx
from filelock import FileLock
from sqlalchemy import select
from agent.settings import ROOT, profile, rules
from agent.registry import load_boards
from agent.models import now, Job
from agent.db import database, CandidateProfile, Company, Source, JobRow, JobObservation, MatchRow, SearchRun, SearchDiagnostic, JobSourceStatus
from agent.collectors import collect, collect_public_source, clear_public_checkpoint
from agent.source_registry import active_api_sources
from agent.matching import filter_reason, canonical, fingerprint, evaluate
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
        row = session.scalar(select(JobRow).where(JobRow.canonical_url==canonical(job.url)).order_by(JobRow.id))
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
    source_status = session.scalar(select(JobSourceStatus).where(
        JobSourceStatus.job_id==row.id, JobSourceStatus.source_id==source.id))
    if source_status is None:
        source_status = JobSourceStatus(job_id=row.id, source_id=source.id)
        session.add(source_status)
    source_status.status = 'Observed in latest source feed'
    source_status.last_seen = now()
    source_status.last_checked = now()
    source_status.expires_at = job.expires_at
    source_status.evidence_url = str(job.source_url or job.url)
    source_status.note = 'Present in the source response; employer availability is not inferred beyond that evidence.'
    session.flush()
    return row, is_new


def record_complete_source_scan(session, source, source_row_id, jobs, scan_started):
    """Record explicit expiry and absence evidence after a complete source walk only."""
    for job in getattr(jobs, 'expired_postings', []):
        identity = f"{job.platform}:{source['board']}:{job.source_id}"
        observation = session.scalar(select(JobObservation).where(JobObservation.identity==identity))
        if observation is None:
            continue
        state = session.scalar(select(JobSourceStatus).where(
            JobSourceStatus.job_id==observation.job_id, JobSourceStatus.source_id==source_row_id))
        if state is None:
            state = JobSourceStatus(job_id=observation.job_id, source_id=source_row_id)
            session.add(state)
        state.status = 'Expired per source'
        state.last_checked = now()
        state.expires_at = job.expires_at
        state.evidence_url = str(job.source_url or job.url)
        state.note = 'Provider supplied an expiry timestamp that has passed.'
    # A complete source response may include a posting now filtered by search rules.
    # Refresh its source observation without creating or recommending a new vacancy.
    expired_ids = {job.source_id for job in getattr(jobs, 'expired_postings', [])}
    for job in jobs:
        if job.source_id in expired_ids:
            continue
        identity = f"{job.platform}:{source['board']}:{job.source_id}"
        observation = session.scalar(select(JobObservation).where(JobObservation.identity==identity))
        if observation is None:
            continue
        state = session.scalar(select(JobSourceStatus).where(
            JobSourceStatus.job_id==observation.job_id, JobSourceStatus.source_id==source_row_id))
        if state is None:
            state = JobSourceStatus(job_id=observation.job_id, source_id=source_row_id)
            session.add(state)
        state.status = 'Observed in latest source feed'
        state.last_seen = now()
        state.last_checked = now()
        state.expires_at = job.expires_at
        state.evidence_url = str(job.source_url or job.url)
        state.note = 'Present in the source response; employer availability is not inferred beyond that evidence.'
    if getattr(jobs, 'pagination_complete', True):
        prior = list(session.scalars(select(JobSourceStatus).where(
            JobSourceStatus.source_id==source_row_id, JobSourceStatus.last_seen < scan_started)))
        for state in prior:
            if state.status == 'Expired per source':
                continue
            state.status = 'Not observed in latest complete source scan'
            state.last_checked = now()
            state.note = 'Not present in the latest complete source response; this alone does not prove the vacancy is closed.'
    session.flush()


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
            diagnostics = {'boards': [], 'totals': {}}
            committed_results = {}
            try:
                candidate, config = profile(), rules()
                boards, registry_errors = load_boards()
                public_sources, source_registry_errors = active_api_sources()
                errors.extend(registry_errors + source_registry_errors)
                payload = candidate.model_dump_json()
                cp = session.scalar(select(CandidateProfile).where(CandidateProfile.payload==payload))
                if cp is None:
                    cp = CandidateProfile(payload=payload)
                    session.add(cp)
                session.commit()
                candidate_id = cp.id
                enabled = [dict(b, source_kind='employer', source_name=b['company']) for b in boards if b['enabled']]
                enabled.extend(dict(company=source['name'], platform=source['id'], board=source['id'],
                                    source_kind='aggregator', source_name=source['name'], source_registry=source)
                               for source in public_sources)
                if not enabled:
                    errors.append('No valid enabled sources configured')
                with httpx.Client(timeout=30,headers={'User-Agent':'QAJobAgent/0.1 (public API reader)'},follow_redirects=True) as client:
                    for board in enabled:
                        report = dict(company=board['company'], source_name=board.get('source_name', board['company']),
                                      platform=board['platform'], board=board['board'], source_kind=board['source_kind'],
                                      published=None, normalized=None, malformed=None, expired=0, pages_fetched=0,
                                      pagination_complete=True, title_rejected=0, relevant=0,
                                      previously_persisted_exclusions=0,
                                      new=0, existing_or_merged=0, committed=False,
                                      main_recommendations=0, excluded_from_main=0,
                                      geography_excluded=0, professional_excluded=0,
                                      stage='collection', rejections={}, eligibility={}, geographic_assessment={},
                                      recommendations={}, professional_fit={})
                        diagnostics['boards'].append(report)
                        scan_started = now()
                        try:
                            checkpoint_path = (ROOT/'data'/'.source_checkpoints'/f"{board['source_registry']['id']}.json"
                                               if board['source_kind'] == 'aggregator' else
                                               ROOT/'data'/'.source_checkpoints'/f"employer-{board['platform']}-{board['board']}.json")
                            jobs = (collect_public_source(board['source_registry'], client,
                                                         config.get('request_interval_seconds',1),
                                                         board['source_registry'].get('max_pages',500),
                                                         checkpoint_path=checkpoint_path)
                                    if board['source_kind'] == 'aggregator' else
                                    collect(board,client,config.get('request_interval_seconds',1),
                                            rate_limit_path=checkpoint_path))
                            warnings = getattr(jobs,'warnings',[])
                            report.update(published=getattr(jobs,'published_count',len(jobs)), normalized=len(jobs),
                                          malformed=len(warnings), expired=getattr(jobs,'expired_count',0),
                                          pages_fetched=getattr(jobs,'pages_fetched',1),
                                          pagination_complete=getattr(jobs,'pagination_complete',True),
                                          source_checked_at=max((job.source_checked_at for job in jobs if job.source_checked_at), default=now()))
                            report['stage'] = 'persistence'
                            rejections, eligibility, geographic, recommendations = Counter(), Counter(), Counter(), Counter()
                            professional = Counter()
                            board_results = {}
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
                                reason = filter_reason(job,config)
                                if reason:
                                    rejections[reason] += 1
                                    # A posting that used to be recommended can stop matching
                                    # current search rules. Keep its history and review status,
                                    # but supersede its stale recommendation with current axes.
                                    identity = f"{job.platform}:{source.board}:{job.source_id}"
                                    observation = session.scalar(select(JobObservation).where(
                                        JobObservation.identity==identity))
                                    existing = (session.get(JobRow,observation.job_id) if observation else
                                                session.scalar(select(JobRow).where(JobRow.identity==identity)))
                                    if existing is not None:
                                        row, _ = upsert_job(session,job,source)
                                        result = evaluate(job,candidate,config,False)
                                        excluded = result.model_copy(update={
                                            'recommendation':'Not Recommended',
                                            'in_main_recommendations':False,
                                            'explanation':f'Excluded from current discovery by search rule: {reason}.'})
                                        version = excluded.version+':'+hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:10]
                                        context = excluded.model_dump() | {
                                            'job_snapshot':job.model_dump(mode='json'),
                                            'search_rules':config,
                                            'discovery_filter_reason':reason}
                                        session.add(MatchRow(job_id=row.id,candidate_id=candidate_id,
                                            payload=json.dumps(context),version=version))
                                        report['previously_persisted_exclusions'] += 1
                                    continue
                                report['relevant'] += 1
                                row, is_new = upsert_job(session,job,source)
                                new_count += int(is_new)
                                result = evaluate(job,candidate,config,use_ai)
                                board_results[row.id] = result
                                eligibility[result.eligibility] += 1
                                geographic[result.geographic_assessment.classification] += 1
                                recommendations[result.recommendation] += 1
                                professional[result.professional_fit] += 1
                                report['main_recommendations'] += int(result.in_main_recommendations)
                                report['excluded_from_main'] += int(not result.in_main_recommendations)
                                report['geography_excluded'] += int(result.eligibility == 'Ineligible')
                                report['professional_excluded'] += int(result.professional_fit == 'Not Recommended')
                                version = result.version+':'+hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:10]
                                # Context is stored with each historical match, not just latest job text.
                                context = result.model_dump() | {'job_snapshot':job.model_dump(mode='json'),'search_rules':config}
                                session.add(MatchRow(job_id=row.id,candidate_id=candidate_id,payload=json.dumps(context),version=version))
                            record_complete_source_scan(session, board, source.id, jobs, scan_started)
                            session.commit()
                            if getattr(jobs, 'pagination_complete', True):
                                clear_public_checkpoint(checkpoint_path)
                            committed_results.update(board_results)
                            report.update(committed=True, new=new_count, existing_or_merged=report['relevant']-new_count,
                                          stage='committed',
                                          title_rejected=sum(rejections.values()), rejections=dict(rejections),
                                          eligibility=dict(eligibility), geographic_assessment=dict(geographic),
                                          recommendations=dict(recommendations), professional_fit=dict(professional))
                            # Count only committed work. Failure rolls back the whole board, then continues.
                            fetched += len(jobs)
                            added += new_count
                            errors.extend(warnings)
                            successes += 1
                            log.info('%s/%s: fetched %s, new relevant %s',board['platform'],board['board'],len(jobs),new_count)
                        except Exception as exc:
                            session.rollback()
                            # SQL/SDK exception bodies can include profile values; store safe diagnostics.
                            policy_status = getattr(exc,'policy_http_status',None)
                            code = (f' HTTP {exc.response.status_code}' if isinstance(exc,httpx.HTTPStatusError)
                                    else f' HTTP {policy_status}' if policy_status else '')
                            message = f"{board['platform']}/{board['board']}: {type(exc).__name__}{code}; board rolled back, processing continues"
                            errors.append(message)
                            report['error'] = message
                            report['policy_http_status'] = policy_status
                            log.warning(message)
                from agent.manual_import import reconcile_imported_leads
                reconcile_imported_leads(session)
                status = 'Failed' if not successes else 'Partial' if errors else 'Success'
            except Exception as exc:
                session.rollback()
                errors.append(f'Search setup failed: {type(exc).__name__}; check local profile/configuration')
                status = 'Failed'
            run = session.get(SearchRun,run_id)
            run.discovered, run.new = fetched, added
            run.status, run.errors, run.finished = status,json.dumps(errors),now()
            totals = Counter()
            totals['active_boards'] = len(diagnostics['boards'])
            totals['active_sources'] = len(diagnostics['boards'])
            totals['committed_boards'] = sum(report['committed'] for report in diagnostics['boards'])
            totals['failed_boards'] = totals['active_boards'] - totals['committed_boards']
            totals['unique_records'] = len(committed_results)
            totals['unique_main_records'] = sum(result.in_main_recommendations for result in committed_results.values())
            totals['unique_excluded_records'] = totals['unique_records'] - totals['unique_main_records']
            totals['unique_main_professionally_suitable'] = sum(
                result.in_main_recommendations and result.professional_fit in {'Strong Match','Good Match'}
                for result in committed_results.values())
            totals['unique_main_needs_eligibility_clarification'] = sum(
                result.in_main_recommendations and result.geographic_assessment.classification in {
                    'Eligible If Relocating','Potentially Eligible — Needs Review'}
                for result in committed_results.values())
            totals['unique_main_confirmed_geographically_eligible'] = sum(
                result.in_main_recommendations and result.geographic_assessment.classification == 'Confirmed Eligible'
                for result in committed_results.values())
            employer_names = set()
            if committed_results:
                persisted_jobs = session.scalars(select(JobRow).where(JobRow.id.in_(committed_results)))
                employer_names = {Job.model_validate_json(row.payload).company for row in persisted_jobs}
            totals['unique_employers_scanned'] = len({
                report['company'] for report in diagnostics['boards']
                if report['source_kind'] == 'employer' and report['committed']})
            totals['unique_employers_represented'] = len(employer_names)
            for result in committed_results.values():
                if result.in_main_recommendations:
                    totals['unique_main_professional_' + result.professional_fit] += 1
                    totals['unique_main_geography_' + result.eligibility] += 1
            for report in diagnostics['boards']:
                for key in ('published','normalized','malformed','expired','pages_fetched'):
                    totals[key] += report[key] or 0
                if report['committed']:
                    for key in ('title_rejected','relevant','new','existing_or_merged','main_recommendations','excluded_from_main','geography_excluded','professional_excluded'):
                        totals[key] += report[key]
                    for key, value in report['eligibility'].items():
                        totals['eligibility_' + key] += value
                        totals['legacy_eligibility_' + key] += value
                    for key, value in report['geographic_assessment'].items():
                        totals['geographic_' + key] += value
                    for key, value in report['rejections'].items():
                        totals['rejected_' + key] += value
                    for key, value in report['recommendations'].items():
                        totals['recommendation_' + key] += value
                    for key, value in report['professional_fit'].items():
                        totals['professional_' + key] += value
                else:
                    totals['rolled_back_normalized'] += report['normalized'] or 0
            diagnostics['totals'] = dict(totals)
            session.add(SearchDiagnostic(run_id=run_id,payload=json.dumps(diagnostics)))
            session.commit()
            log.info('Search %s finished: %s; fetched=%s new=%s errors=%s',run_id,status,fetched,added,len(errors))
            return {'id':run_id,'status':status,'fetched':fetched,'new_relevant':added,'errors':errors,'diagnostics':diagnostics}
