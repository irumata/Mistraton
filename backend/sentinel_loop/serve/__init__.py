"""Production stage: self-hosted Timestral serving via FastAPI /watch."""

from .watcher import create_app, create_sentinel_router

__all__ = ["create_app", "create_sentinel_router"]
