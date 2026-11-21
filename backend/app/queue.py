from redis import Redis
from rq import Queue
from rq.serializers import JSONSerializer

from app.config import settings


def redis_connection() -> Redis:
    return Redis.from_url(settings().redis_url, socket_connect_timeout=5, socket_timeout=5)


def queue() -> Queue:
    return Queue("default", connection=redis_connection(), serializer=JSONSerializer)
