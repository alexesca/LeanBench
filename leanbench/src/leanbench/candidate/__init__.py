"""Candidate side of the boundary: manifest, transport, runner, digests, classification."""

from leanbench.candidate.classify import CLASSIFICATION_MATRIX, classify, classify_error_response
from leanbench.candidate.digests import binary_digest, config_digest, digest_bytes, digest_file
from leanbench.candidate.manifest import load_manifest
from leanbench.candidate.runner import CandidateResponse, SubprocessCandidate

__all__ = [
    "CLASSIFICATION_MATRIX",
    "CandidateResponse",
    "SubprocessCandidate",
    "binary_digest",
    "classify",
    "classify_error_response",
    "config_digest",
    "digest_bytes",
    "digest_file",
    "load_manifest",
]
