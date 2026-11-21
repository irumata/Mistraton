"""Example job for the optional Redis/RQ worker; no public enqueue endpoint."""


def add(left: int, right: int) -> int:
    return left + right
