"""TraceFetch public package surface."""

from tracefetch.contracts import (
    EvidenceReceipt,
    ProviderManifest,
    SearchEnvelope,
    SearchResultsEnvelope,
)
from tracefetch.unified import search_everywhere

__all__ = [
    "EvidenceReceipt",
    "ProviderManifest",
    "SearchEnvelope",
    "SearchResultsEnvelope",
    "search_everywhere",
]
__version__ = "1.0.1"
