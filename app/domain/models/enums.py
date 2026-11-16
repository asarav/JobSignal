from __future__ import annotations

from enum import StrEnum


class WorkplaceType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    INTERN = "intern"
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    MANAGER = "manager"
    DIRECTOR = "director"
    EXECUTIVE = "executive"
    UNKNOWN = "unknown"


class PostingStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    REMOVED = "removed"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class ExtractionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class RepostClass(StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"
    REPOSTED_IDENTICAL = "reposted_identical"
    MATERIALLY_UPDATED = "materially_updated"
    SIMILAR_DISTINCT = "similar_distinct"
    NEW_POSITION = "new_position"


class ApplicationStatus(StrEnum):
    INTERESTED = "interested"
    SAVED = "saved"
    APPLIED = "applied"
    ACKNOWLEDGED = "acknowledged"
    RECRUITER_CONTACTED = "recruiter_contacted"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    OFFERED = "offered"
    ACCEPTED = "accepted"


class OutcomeKind(StrEnum):
    NO_RESPONSE = "no_response"
    ACKNOWLEDGED = "acknowledged"
    RECRUITER_CONTACT = "recruiter_contact"
    INTERVIEW = "interview"
    REJECTION = "rejection"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"
    ACCEPTED = "accepted"


class UserJobState(StrEnum):
    NEW = "new"
    SAVED = "saved"
    DISMISSED = "dismissed"
    HIDDEN = "hidden"


class RiskClass(StrEnum):
    LIKELY_ACTIVE = "likely_active"
    NEEDS_VERIFICATION = "needs_verification"
    LIKELY_STALE = "likely_stale"
    LIKELY_INACTIVE = "likely_inactive"


class SignalStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    ERROR = "error"


class SuppressionState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    RESTORED = "restored"


class SuppressionReason(StrEnum):
    EXACT_REPOST_OF_APPLIED = "exact_repost_of_applied"
    SIMILAR_ROLE_COOLDOWN = "similar_role_cooldown"
    COMPANY_WIDE_COOLDOWN = "company_wide_cooldown"


class SkillEvidence(StrEnum):
    EXPLICIT_WITH_EXPERIENCE = "explicit_with_experience"
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    LIMITED = "limited"


class UserSkillStatus(StrEnum):
    HAVE_EXPERIENCE = "have_experience"
    LEARNING = "learning"
    NO_EXPERIENCE = "no_experience"
    NOT_RELEVANT = "not_relevant"


class RequirementLevel(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    MENTIONED = "mentioned"


class CrawlRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class EndpointState(StrEnum):
    OK = "ok"
    CLOSED_MESSAGE = "closed_message"
    FORM_UNAVAILABLE = "form_unavailable"
    GONE_404_410 = "gone_404_410"
    GENERIC_REDIRECT = "generic_redirect"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"
