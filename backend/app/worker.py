from rq import Worker
from rq.serializers import JSONSerializer

from app.queue import queue, redis_connection

if __name__ == "__main__":
    # Default forking Worker is intended for the Linux container, not native Windows/macOS.
    Worker([queue()], connection=redis_connection(), serializer=JSONSerializer).work()
