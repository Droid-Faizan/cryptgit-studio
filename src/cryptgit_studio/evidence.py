"""Evidence collection primitives."""

from .models import Evidence


def create_evidence(
    source: str, description: str, confidence: float = 1.0
) -> Evidence:
    """Create validated evidence for later analysis."""
    if not source.strip():
        raise ValueError("Evidence source must not be empty")
    if not description.strip():
        raise ValueError("Evidence description must not be empty")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("Evidence confidence must be between 0 and 1")
    return Evidence(
        source=source,
        description=description,
        confidence=confidence,
    )
