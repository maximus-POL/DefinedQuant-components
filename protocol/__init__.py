"""Public models for deterministic, inspectable Defined Quant operations."""

import sys
from pathlib import Path

# Hatch maps this readable source folder to ``defined_quant_protocol`` in a wheel. Its editable
# wheel adds the source folder itself to ``sys.path``. Extend this package path so the
# force-included bootstrap module continues to resolve live source modules during development.
for _entry in sys.path:
    _candidate = Path(_entry)
    if (
        _candidate.name == "protocol"
        and (_candidate / "canonical.py").is_file()
        and (_candidate / "operation.py").is_file()
    ):
        __path__.insert(0, str(_candidate))
        break

from .canonical import (  # noqa: E402
    CANONICALIZATION_ID,
    canonical_hash,
    canonical_json_bytes,
)
from .operation import (  # noqa: E402
    CallerProvenance,
    ComponentRef,
    FileDigest,
    InterpretationMethod,
    OperationError,
    OperationErrorCode,
    OperationFailure,
    OperationManifest,
    OperationProvenance,
    OperationRequest,
    OperationResult,
    OperationSuccess,
    ProvenanceStatus,
    RelativeMemberPath,
    RunnerIdentity,
    SourceKind,
    SvgArtifact,
    SvgArtifactRequest,
    VerificationStatus,
    operation_hash,
    operation_protocol_schema,
)
from .version import PROTOCOL_VERSION  # noqa: E402

__version__ = PROTOCOL_VERSION

__all__ = [
    "PROTOCOL_VERSION",
    "CANONICALIZATION_ID",
    "CallerProvenance",
    "ComponentRef",
    "FileDigest",
    "InterpretationMethod",
    "OperationError",
    "OperationErrorCode",
    "OperationFailure",
    "OperationManifest",
    "OperationProvenance",
    "OperationRequest",
    "OperationResult",
    "OperationSuccess",
    "ProvenanceStatus",
    "RelativeMemberPath",
    "RunnerIdentity",
    "SourceKind",
    "SvgArtifact",
    "SvgArtifactRequest",
    "VerificationStatus",
    "canonical_hash",
    "canonical_json_bytes",
    "operation_hash",
    "operation_protocol_schema",
]

del _candidate, _entry
