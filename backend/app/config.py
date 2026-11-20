import os
from functools import lru_cache

from pydantic import BaseModel, Field


class Settings(BaseModel):
    database_url: str
    redis_url: str
    admin_api_key: str = Field(min_length=32)
    webhook_secret: str = Field(min_length=32)
    job_timeout: int = Field(default=1200, ge=1, le=86400)


@lru_cache
def settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        admin_api_key=os.environ["ADMIN_API_KEY"],
        webhook_secret=os.environ["WEBHOOK_SECRET"],
        job_timeout=int(os.getenv("JOB_TIMEOUT", "1200")),
    )
