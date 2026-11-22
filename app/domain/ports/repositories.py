"""Replaceable repository ports. Domain depends only on these Protocols."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from app.domain.models.application import (
    Application,
    ApplicationEvent,
    ApplicationOutcome,
    AppliedJob,
    SuppressionRecord,
)
from app.domain.models.candidate import SearchProfile
from app.domain.models.identity import JobFingerprintSet
from app.domain.models.job import JobView, NormalizedJob
from app.domain.models.source import SourceConfig
from app.domain.models.tasks import BackgroundTask


class SettingsRepository(Protocol):
    async def get(self, key: str) -> Mapping[str, object] | None: ...

    async def get_all(self) -> Mapping[str, Mapping[str, object]]: ...

    async def put(
        self, key: str, value: Mapping[str, object], *, schema_version: int = 1
    ) -> None: ...


class SearchProfileRepository(Protocol):
    async def add(self, profile: SearchProfile) -> None: ...

    async def get(self, profile_id: str) -> SearchProfile | None: ...

    async def list(self) -> Sequence[SearchProfile]: ...

    async def save(self, profile: SearchProfile) -> None: ...

    async def delete(self, profile_id: str) -> None: ...


class TaskRepository(Protocol):
    async def add(self, task: BackgroundTask) -> None: ...

    async def get(self, task_id: str) -> BackgroundTask | None: ...

    async def list(
        self, *, status: str | None = None, limit: int = 50
    ) -> Sequence[BackgroundTask]: ...

    async def save(self, task: BackgroundTask) -> None: ...

    async def mark_interrupted(self) -> int:
        """Mark queued/running tasks left by a previous process as interrupted."""
        ...


class SourceRepository(Protocol):
    async def add(self, source: SourceConfig) -> None: ...

    async def get(self, source_id: str) -> SourceConfig | None: ...

    async def list(self, *, enabled_only: bool = False) -> Sequence[SourceConfig]: ...

    async def set_enabled(self, source_id: str, enabled: bool) -> None: ...

    async def save(self, source: SourceConfig) -> None: ...

    async def delete(self, source_id: str) -> None: ...


class CompanyRepository(Protocol):
    async def upsert(self, company_id: str, canonical_name: str, **fields: object) -> None: ...

    async def get(self, company_id: str) -> Mapping[str, object] | None: ...

    async def find_by_name(self, normalized_name: str) -> str | None: ...

    async def list(self) -> Sequence[Mapping[str, object]]: ...


class JobRepository(Protocol):
    async def upsert(
        self,
        job_id: str,
        *,
        company_id: str,
        source_id: str,
        job: NormalizedJob | JobView,
        content_hash: str | None = None,
        updated_at_hint: str | None = None,
    ) -> None: ...

    async def get(self, job_id: str) -> Mapping[str, object] | None: ...

    async def find_by_canonical_url(self, canonical_url: str) -> str | None: ...

    async def find_by_source_posting(
        self, source_id: str, source_posting_id: str
    ) -> str | None: ...

    async def list(
        self,
        *,
        company_id: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        exclude_job_ids: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, object]]: ...

    async def list_for_sources(
        self, source_ids: Sequence[str], *, limit: int = 50
    ) -> Mapping[str, Sequence[Mapping[str, object]]]:
        """Up to ``limit`` postings per source, keyed by source id."""
        ...

    async def clear_provenance_note(self, job_id: str, note: str) -> bool:
        """Remove a note from every provenance entry; ``True`` when changed."""
        ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
        exclude_job_ids: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, object]]: ...

    async def filtered_page(
        self,
        filters: Mapping[str, object],
        *,
        query: str | None = None,
        source_id: str | None = None,
        company_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        exclude_job_ids: Sequence[str] | None = None,
    ) -> tuple[Sequence[Mapping[str, object]], int]:
        """Return one filtered page plus the total number of matches."""
        ...

    async def count(self, *, exclude_job_ids: Sequence[str] | None = None) -> int: ...

    async def mark_not_seen(
        self, source_id: str, seen_ids: Sequence[str], cutoff: datetime,
        threshold: int = 2, min_spacing_hours: int = 24,
    ) -> int: ...

    async def add_relation(
        self, job_id: str, related_job_id: str, relation: str,
        confidence: float = 1.0, evidence: Sequence[str] | None = None,
        created_by: str = "user",
    ) -> None: ...
    async def remove_relation(
        self, job_id: str, related_job_id: str, relation: str
    ) -> int:
        """Remove a relation in either direction; returns rows deleted."""
        ...
    async def list_relations(self, job_id: str) -> Sequence[Mapping[str, object]]: ...
    async def relation_job_ids(self) -> Sequence[str]:
        """Every job id that appears on either side of a relation."""
        ...
    async def list_by_ids(
        self, job_ids: Sequence[str]
    ) -> Sequence[Mapping[str, object]]: ...
    async def touch(self, job_id: str, seen_at: datetime) -> None:
        """Refresh ``last_seen_at`` without changing content."""
        ...


class ObservationRepository(Protocol):
    async def add(
        self,
        *,
        job_id: str,
        source_id: str,
        observed_at: datetime,
        posting_status: str,
        details: Mapping[str, object] | None = None,
    ) -> None: ...

    async def list_for_job(self, job_id: str) -> Sequence[Mapping[str, object]]: ...


class DedupRepository(Protocol):
    """Fingerprint history and repost events (spec §7.2 / §7.4)."""

    async def add_fingerprint(
        self,
        job_id: str,
        fingerprint: JobFingerprintSet,
        *,
        snapshot_id: str | None,
        created_at: datetime,
    ) -> None: ...

    async def candidate_fingerprints(
        self, company_id: str, *, limit: int = 200
    ) -> Sequence[Mapping[str, object]]:
        """Each of the company's recent jobs with its latest fingerprint."""
        ...

    async def add_repost_event(
        self,
        *,
        job_id: str,
        previous_alias_id: str | None,
        new_alias_id: str | None,
        detected_at: datetime,
        repost_class: str,
        confidence: float,
        url_changed: bool = False,
        ats_id_changed: bool = False,
        posted_date_reset: bool = False,
        material: bool = False,
        evidence: Sequence[str] = (),
    ) -> None: ...

    async def list_repost_events(
        self, job_id: str
    ) -> Sequence[Mapping[str, object]]: ...


class SnapshotRepository(Protocol):
    """Append-only history of a job's content (spec §23.8)."""

    async def add(
        self,
        *,
        job_id: str,
        content_hash: str,
        snapshot: Mapping[str, object],
        created_at: datetime,
    ) -> None: ...

    async def list_for_job(self, job_id: str) -> Sequence[Mapping[str, object]]: ...


class AliasRepository(Protocol):
    """Alternative URLs that resolve to the same job."""

    async def add(
        self,
        *,
        job_id: str,
        alias_url: str,
        alias_source_id: str | None,
        created_at: datetime,
    ) -> None: ...

    async def find_by_url(self, alias_url: str) -> str | None: ...

    async def remove(self, job_id: str, alias_url: str) -> bool:
        """Drop an alias recorded for a job; ``True`` when it existed."""
        ...

    async def list_for_job(self, job_id: str) -> Sequence[Mapping[str, object]]: ...


class CrawlRunRepository(Protocol):
    async def create(self, run_id: str, source_id: str, started_at: datetime) -> None: ...

    async def finish(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime,
        stats: Mapping[str, object],
        error: str | None = None,
    ) -> None: ...

    async def get(self, run_id: str) -> Mapping[str, object] | None: ...

    async def list(
        self, *, source_id: str | None = None, limit: int = 50
    ) -> Sequence[Mapping[str, object]]: ...

    async def last_started_by_source(self) -> Mapping[str, str]:
        """Most recent ``started_at`` (ISO 8601) per source id."""
        ...

    async def mark_interrupted(self) -> int:
        """Close crawl runs that were still running when the worker stopped."""
        ...


class ResumeRepository(Protocol):
    async def add_profile(self, profile_id: str, display_name: str) -> None: ...
    async def get_profile(self, profile_id: str) -> Mapping[str, object] | None: ...
    async def add_resume_file(self, file_id: str, sha256: str, original_name: str, mime_type: str, size_bytes: int, stored_path: str) -> None: ...
    async def get_resume_file_by_sha256(self, sha256: str) -> Mapping[str, object] | None: ...
    async def add_resume_version(self, version_id: str, profile_id: str, file_id: str, label: str, parser_id: str, parser_version: str, text_hash: str, parsed_json: str, parent_version_id: str | None = None, is_active: bool = False) -> None: ...
    async def list_versions(self, profile_id: str) -> Sequence[Mapping[str, object]]: ...
    async def set_active(self, profile_id: str, version_id: str) -> None: ...
    async def get_version(self, version_id: str) -> Mapping[str, object] | None: ...
    async def save_skills(
        self, version_id: str, skills: Sequence[Mapping[str, object]]
    ) -> None: ...
    async def get_skills(self, version_id: str) -> Sequence[Mapping[str, object]]: ...
    async def save_experience(
        self, version_id: str, entries: Sequence[Mapping[str, object]]
    ) -> None: ...
    async def get_experience(self, version_id: str) -> Sequence[Mapping[str, object]]: ...
    async def save_education(
        self, version_id: str, entries: Sequence[Mapping[str, object]]
    ) -> None: ...
    async def get_education(self, version_id: str) -> Sequence[Mapping[str, object]]: ...
    async def save_profile_items(
        self, version_id: str, kind: str, items: Sequence[Mapping[str, object]]
    ) -> None: ...
    async def get_profile_items(
        self, version_id: str, kind: str
    ) -> Sequence[Mapping[str, object]]: ...
    async def update_overrides(self, profile_id: str, overrides: Mapping[str, object]) -> None: ...
    async def get_overrides(self, profile_id: str) -> Mapping[str, object]: ...
    async def update_contact(self, profile_id: str, contact: Mapping[str, object]) -> None: ...
    async def get_resume_file(self, file_id: str) -> Mapping[str, object] | None: ...
    async def set_skill_status(self, profile_id: str, keyword_id: str, status: str) -> None: ...
    async def get_skill_status(self, profile_id: str) -> Mapping[str, str]: ...



class MatchRepository(Protocol):
    async def save_result(
        self,
        result_id: str,
        job_id: str,
        profile_id: str,
        resume_version_id: str | None,
        model_id: str,
        model_version: str,
        weights: Mapping[str, object],
        input_hash: str,
        fit_score: float,
        dimension_coverage: float,
        dimensions: list[dict[str, Any]],
        evaluated_at: str,
        explanations: list[dict[str, Any]] | None = None,
    ) -> None: ...
    async def list_results(self, profile_id: str, limit: int = 50) -> Sequence[Mapping[str, object]]: ...
    async def find_result(
        self, profile_id: str, job_id: str, input_hash: str
    ) -> Mapping[str, object] | None: ...
    async def delete_results(self, profile_id: str, job_id: str) -> None: ...
    async def list_dimensions(self, result_id: str) -> Sequence[Mapping[str, object]]: ...
    async def list_explanations(self, result_id: str) -> Sequence[Mapping[str, object]]: ...
    async def get_weights(self, profile_id: str) -> Mapping[str, object] | None: ...
    async def set_weights(self, profile_id: str, weights: Mapping[str, object]) -> None: ...



class KeywordRepository(Protocol): ...


class ApplicationRepository(Protocol):
    """Application history with an append-only event log (spec §8.1)."""

    async def add(self, application: Application) -> None: ...

    async def get(self, application_id: str) -> Application | None: ...

    async def list(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[Application]: ...

    async def save(
        self,
        application_id: str,
        *,
        status: str,
        applied_at: datetime | None,
        notes: str,
        resume_version_id: str | None,
        updated_at: datetime,
    ) -> Application | None: ...

    async def find_for_job(self, job_id: str) -> Sequence[Application]: ...

    async def list_applied(
        self, *, company_id: str | None = None
    ) -> Sequence[AppliedJob]:
        """Applications that reached ``applied``, for suppression evaluation."""
        ...

    async def add_event(self, event: ApplicationEvent) -> None: ...

    async def list_events(self, application_id: str) -> Sequence[ApplicationEvent]: ...

    async def add_outcome(self, outcome: ApplicationOutcome) -> None: ...

    async def list_outcomes(
        self, application_id: str
    ) -> Sequence[ApplicationOutcome]: ...


class SuppressionRepository(Protocol):
    """Stored suppression decisions and their state transitions (spec §8.2)."""

    async def save(self, record: SuppressionRecord) -> None:
        """Insert or refresh a decision; a restored pair stays restored."""
        ...

    async def get(
        self, job_id: str, application_id: str
    ) -> SuppressionRecord | None: ...

    async def list(
        self, *, state: str | None = None, limit: int = 200
    ) -> Sequence[SuppressionRecord]: ...

    async def list_for_job(self, job_id: str) -> Sequence[SuppressionRecord]: ...

    async def active_job_ids(self) -> Sequence[str]: ...

    async def restore(self, job_id: str, application_id: str, at: datetime) -> bool: ...

    async def expire(self, now: datetime) -> Sequence[SuppressionRecord]:
        """Lapse every due decision and return the rows that expired."""
        ...

    async def clear_restored(self, job_id: str, application_id: str) -> bool: ...

    async def delete_for_application(self, application_id: str) -> int: ...

    async def clear_job(self, job_id: str) -> int:
        """Drop non-restored decisions for a job; a restore always survives."""
        ...

    async def get_policy(self) -> Mapping[str, object] | None:
        """The stored default policy, or ``None`` when the user never changed it."""
        ...

    async def put_policy(
        self, policy: Mapping[str, object], at: datetime
    ) -> None: ...


class JobUserStateRepository(Protocol):
    """Saved/dismissed/hidden state; never an application (spec §8.1)."""

    async def set(
        self, job_id: str, state: str, *, note: str | None, updated_at: datetime
    ) -> None: ...

    async def get(self, job_id: str) -> Mapping[str, object] | None: ...

    async def get_many(self, job_ids: Sequence[str]) -> Mapping[str, str]: ...

    async def list(
        self, *, state: str | None = None, limit: int = 200
    ) -> Sequence[Mapping[str, object]]: ...

    async def hidden_job_ids(self) -> Sequence[str]: ...

    async def clear(self, job_id: str) -> bool: ...


class PluginStateRepository(Protocol):
    async def upsert_plugin(
        self,
        plugin_id: str,
        kind: str,
        version: str,
        api_version: str,
        *,
        enabled: bool = True,
        health: Mapping[str, object] | None = None,
    ) -> None: ...

    async def list_plugins(self) -> Sequence[Mapping[str, object]]: ...

    async def set_enabled(self, plugin_id: str, enabled: bool) -> None: ...

    async def get_health(self, plugin_id: str) -> Mapping[str, object]: ...

    async def set_health(self, plugin_id: str, health: Mapping[str, object]) -> None: ...

    async def get_config(self, plugin_id: str) -> Mapping[str, object] | None: ...

    async def set_config(self, plugin_id: str, config: Mapping[str, object]) -> None: ...


class UnitOfWork(Protocol):
    jobs: JobRepository
    companies: CompanyRepository
    applications: ApplicationRepository
    suppressions: SuppressionRepository
    job_user_state: JobUserStateRepository
    settings: SettingsRepository
    observations: ObservationRepository
    resumes: ResumeRepository
    matches: MatchRepository
    keywords: KeywordRepository
    tasks: TaskRepository
    plugins: PluginStateRepository
    sources: SourceRepository
    crawl_runs: CrawlRunRepository
    snapshots: SnapshotRepository
    aliases: AliasRepository
    dedup: DedupRepository
    search_profiles: SearchProfileRepository

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
