"""Integrated local runtime and platform-selected collector transport."""

from .application import IntegratedApplication, create_collection_application
from .collector_config import CollectorRuntimeConfigError, load_collector_config
from .config import OrchestrationSettings, load_orchestration_settings
from .named_pipe import (
    LocalTransportError,
    MacOSUnixSocketServer,
    NamedPipeError,
    WindowsNamedPipeServer,
    make_local_transport_server,
)
from .orchestrator import RuntimeOrchestrator, StreamEmission
from .profiles import ActiveProfileError, DirectoryProfileProvider
from .service import IntegratedRuntimeService

__all__ = [
    "IntegratedRuntimeService",
    "IntegratedApplication",
    "ActiveProfileError",
    "CollectorRuntimeConfigError",
    "DirectoryProfileProvider",
    "NamedPipeError",
    "LocalTransportError",
    "MacOSUnixSocketServer",
    "OrchestrationSettings",
    "RuntimeOrchestrator",
    "StreamEmission",
    "WindowsNamedPipeServer",
    "make_local_transport_server",
    "load_orchestration_settings",
    "load_collector_config",
    "create_collection_application",
]
