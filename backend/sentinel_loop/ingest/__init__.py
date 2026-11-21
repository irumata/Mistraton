"""Ingestion & normalization layer: any incident type -> IncidentRecord JSONL."""

from .loader import ingest_directory, load_records, write_records

__all__ = ["ingest_directory", "load_records", "write_records"]
