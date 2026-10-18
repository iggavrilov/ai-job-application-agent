"""Transactional, idempotent recovery of historical fuzzy vacancy groups.

No observations or historical match payloads are deleted. Distinct application
URLs receive separate rows; exact URL aliases may still share a vacancy.
"""
import json
from collections import defaultdict
from sqlalchemy import select, func
from agent.db import JobRow, JobObservation, MatchRow, CandidateProfile
from agent.models import Job, Candidate
from agent.matching import canonical, fingerprint, evaluate
from agent.settings import rules


def repair_grouped_jobs(session):
    grouped_ids = list(session.scalars(select(JobObservation.job_id).group_by(
        JobObservation.job_id).having(func.count(JobObservation.id) > 1)))
    repaired = 0
    for job_id in grouped_ids:
        row = session.get(JobRow, job_id)
        observations = list(session.scalars(select(JobObservation).where(
            JobObservation.job_id == job_id).order_by(JobObservation.id)))
        groups = defaultdict(list)
        for observation in observations:
            groups[canonical(Job.model_validate_json(observation.payload).url)].append(observation)
        if len(groups) < 2:
            continue
        primary = next((o for o in observations if o.identity == row.identity), observations[0])
        primary_url = canonical(Job.model_validate_json(primary.payload).url)
        history = list(session.scalars(select(MatchRow).where(MatchRow.job_id == job_id).order_by(MatchRow.id)))
        targets = {}
        for url, group in groups.items():
            latest = max(group, key=lambda o: (o.last_checked, o.id))
            job = Job.model_validate_json(latest.payload)
            target = row if url == primary_url else JobRow(status='New')
            if target is not row:
                session.add(target)
            target.identity = group[0].identity
            target.source_id = group[0].source_id
            target.canonical_url = url
            target.fingerprint = fingerprint(job)
            target.first_discovered = min(o.first_discovered for o in group)
            target.last_checked = max(o.last_checked for o in group)
            target.payload = job.model_copy(update={'discovered': target.first_discovered}).model_dump_json()
            session.flush()
            targets[url] = target
            for observation in group:
                observation.job_id = target.id
        # Only evidence tied to a source snapshot is moved. Payloads stay intact.
        assigned = set()
        for match in history:
            snapshot = json.loads(match.payload).get('job_snapshot')
            url = canonical(snapshot['url']) if snapshot else None
            if url in targets:
                match.job_id = targets[url].id
                assigned.add(url)
        # Unsnapshotted historical assessments are not trusted for either job.
        # Append a fresh independent assessment using the saved candidate only.
        for url, target in targets.items():
            if url in assigned or not history:
                continue
            previous = history[-1]
            candidate = session.get(CandidateProfile, previous.candidate_id)
            if candidate is None:
                # Keep an auditable fail-closed match rather than hiding the job.
                payload = json.loads(previous.payload) | {
                    'professional_score': None, 'in_main_recommendations': False,
                    'eligibility': 'Needs Review', 'recommendation': 'Needs Review',
                    'exclusion_reasons': ['Recovered source posting lacks a candidate snapshot; reassessment required.']}
                version = 'identity-repair-unverified'
            else:
                job = Job.model_validate_json(target.payload)
                config = rules()
                result = evaluate(job, Candidate.model_validate_json(candidate.payload), config, False)
                payload = result.model_dump() | {'job_snapshot': job.model_dump(mode='json'), 'search_rules': config}
                version = result.version + ':identity-repair'
            session.add(MatchRow(job_id=target.id, candidate_id=previous.candidate_id,
                                 payload=json.dumps(payload), version=version))
        repaired += len(groups) - 1
    session.flush()
    return repaired
