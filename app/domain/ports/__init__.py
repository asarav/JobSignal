"""Replaceable ports. Domain depends only on these Protocols."""

from __future__ import annotations

from app.domain.ports.browser import BrowserProvider, FormFiller, FormInspector
from app.domain.ports.clock import Clock, IdGenerator
from app.domain.ports.connectors import JobSourceConnector
from app.domain.ports.dedup import DeduplicationStrategy, JobIdentityResolver
from app.domain.ports.discovery import DiscoveryProvider
from app.domain.ports.events import EventPublisher
from app.domain.ports.extraction import ExtractionStrategy, JobNormalizer
from app.domain.ports.http import HttpClient
from app.domain.ports.logging import StructuredLogger
from app.domain.ports.matching import MatchingStrategy, ScoringModel
from app.domain.ports.models import ModelProvider
from app.domain.ports.plugins import PluginManifest, PluginRegistryPort
from app.domain.ports.posting_intelligence import GhostJobRiskModel, PostingSignalProvider
from app.domain.ports.repositories import UnitOfWork, UnitOfWorkFactory
from app.domain.ports.resume import (
    KeywordExtractor,
    KeywordNormalizer,
    ResumeParser,
)

__all__ = [
    "BrowserProvider",
    "Clock",
    "DeduplicationStrategy",
    "DiscoveryProvider",
    "EventPublisher",
    "ExtractionStrategy",
    "FormFiller",
    "FormInspector",
    "GhostJobRiskModel",
    "HttpClient",
    "IdGenerator",
    "JobIdentityResolver",
    "JobNormalizer",
    "JobSourceConnector",
    "KeywordExtractor",
    "KeywordNormalizer",
    "MatchingStrategy",
    "ModelProvider",
    "PostingSignalProvider",
    "PluginManifest",
    "PluginRegistryPort",
    "ResumeParser",
    "ScoringModel",
    "StructuredLogger",
    "UnitOfWork",
    "UnitOfWorkFactory",
]