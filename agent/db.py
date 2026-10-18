import os
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, UniqueConstraint, event
from sqlalchemy.orm import declarative_base, sessionmaker
from agent.settings import ROOT
from agent.models import now
Base = declarative_base()


class CandidateProfile(Base):
    __tablename__ = 'candidate_profiles'
    id = Column(Integer, primary_key=True)
    payload = Column(Text, nullable=False)
    created = Column(String, default=now)


class Company(Base):
    __tablename__ = 'companies'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)


class Source(Base):
    __tablename__ = 'job_sources'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey('companies.id'))
    platform = Column(String)
    board = Column(String)
    __table_args__ = (UniqueConstraint('platform', 'board'),)


class JobRow(Base):
    __tablename__ = 'jobs'
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('job_sources.id'))
    identity = Column(String, unique=True)
    canonical_url = Column(String, index=True)
    fingerprint = Column(String, index=True)
    payload = Column(Text)
    first_discovered = Column(String, default=now)
    last_checked = Column(String, default=now)
    status = Column(String, default='New')


class JobObservation(Base):
    """Retain each source identity and payload even when jobs merge across boards."""
    __tablename__ = 'job_observations'
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('jobs.id'), nullable=False)
    source_id = Column(Integer, ForeignKey('job_sources.id'), nullable=False)
    identity = Column(String, unique=True, nullable=False)
    canonical_url = Column(String, index=True)
    payload = Column(Text, nullable=False)
    first_discovered = Column(String, default=now)
    last_checked = Column(String, default=now)


class MatchRow(Base):
    __tablename__ = 'job_matches'
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('jobs.id'))
    candidate_id = Column(Integer, ForeignKey('candidate_profiles.id'))
    payload = Column(Text)
    version = Column(String)
    created = Column(String, default=now)


class SearchRun(Base):
    __tablename__ = 'search_runs'
    id = Column(Integer, primary_key=True)
    started = Column(String, default=now)
    finished = Column(String)
    status = Column(String, default='Running')
    discovered = Column(Integer, default=0)
    new = Column(Integer, default=0)
    errors = Column(Text, default='[]')


class ApplicationRecord(Base):
    __tablename__ = 'application_records'
    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey('jobs.id'), unique=True)
    created = Column(String, default=now)
    status = Column(String)


def database(url=None):
    (ROOT / 'data').mkdir(exist_ok=True)
    from sqlalchemy.engine import make_url
    target = make_url(url or os.getenv('DATABASE_URL', 'sqlite:///' + str(ROOT / 'data/jobs.db')))
    if target.get_backend_name() != 'sqlite':
        raise ValueError('MVP-01 supports SQLite only')
    if target.database and target.database != ':memory:':
        from pathlib import Path
        path = Path(target.database)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        target = target.set(database=str(path))
    engine = create_engine(target, connect_args={'timeout':30})
    @event.listens_for(engine, 'connect')
    def sqlite_options(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
