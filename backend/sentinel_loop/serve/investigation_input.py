"""Read an investigation ZIP without extracting files into shared storage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import zipfile
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path, PurePosixPath
from statistics import median

import numpy as np

from ..archive import ARCHIVE_VERSION, MANIFEST_NAME
from ..labeling.pii import regex_pii_filter

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 256
MAX_ATTACHMENTS = 8
MAX_GRID = 720
MIN_OBSERVATIONS = 16
MICROSECOND = timedelta(microseconds=1)
MICROSECONDS_PER_SECOND = timedelta(seconds=1) // MICROSECOND
TIMESTAMP_HEADERS = {'timestamp', 'ts', 'time', 'datetime', 'date'}
TEXT_EXTENSIONS = {'.log', '.txt', '.md', '.json', '.jsonl'}
EVIDENCE_EXTENSIONS = TEXT_EXTENSIONS | {'.csv'}
COMPACT_HTTP = re.compile(
    r'^(?P<ts>\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)\s+\S+\s+\S+\s+\S+\s+'
    r'(?P<status>[1-5]\d\d)\s+(?P<duration>\d+(?:\.\d+)?)ms\b'
)
ACCESS_HTTP = re.compile(r'\[(?P<ts>\d\d/[A-Za-z]{3}/\d{4}:\d\d:\d\d:\d\d [+-]\d{4})\]\s+"[^"]*"\s+(?P<status>[1-5]\d\d)\b')


def timestamp(value: str) -> datetime:
    value = value.strip()
    if '/' in value:
        parsed = datetime.strptime(value, '%d/%b/%Y:%H:%M:%S %z')
    else:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


@dataclass(frozen=True)
class Series:
    id: str
    name: str
    source: str
    source_field: str
    observations: int
    start: datetime
    interval: timedelta
    values: np.ndarray

    def time(self, index: int) -> str:
        return (self.start + self.interval * int(index)).isoformat()

    def describe(self) -> dict:
        observed = self.values[np.isfinite(self.values)]
        return {'id': self.id, 'metric': self.name, 'source': self.source, 'source_field': self.source_field,
                'measurements': self.observations,
                'populated_intervals': len(observed), 'intervals': len(self.values),
                'first_interval': self.time(0), 'last_interval': self.time(len(self.values) - 1),
                'interval_seconds': self.interval.total_seconds(), 'min': float(np.min(observed)),
                'median': float(np.median(observed)), 'max': float(np.max(observed)),
                'eligible_for_detection': len(observed) >= MIN_OBSERVATIONS}


def regularize(id: str, name: str, source: str, source_field: str, points: list[tuple[datetime, float]], *,
               sampling_interval: timedelta | None = None) -> Series:
    start = min(ts for ts, _ in points)
    offsets = [(ts - start) // MICROSECOND for ts, _ in points]
    times = sorted(set(offsets))
    gaps = [end - start for start, end in zip(times, times[1:])]
    # Preserve second-aligned grids and use finer intervals for fractional samples.
    resolution = MICROSECONDS_PER_SECOND
    if any(ts.microsecond for ts, _ in points):
        resolution = 1 if any(ts.microsecond % 1000 for ts, _ in points) else 1000
    preferred = round(median(gaps) / (2 * resolution)) if gaps else 1
    if sampling_interval is not None:
        preferred = sampling_interval // MICROSECOND // resolution
    grid_span = (MAX_GRID - 1) * resolution
    minimum = (times[-1] + grid_span - 1) // grid_span
    interval = max(1, preferred, minimum) * resolution
    bins: dict[int, list[float]] = defaultdict(list)
    for offset, (_, value) in zip(offsets, points):
        bins[offset // interval].append(value)
    values = np.full(max(bins) + 1, np.nan, dtype=np.float32)
    for index, readings in bins.items():
        values[index] = float(np.mean(readings, dtype=np.float64))
    return Series(id, name, source, source_field, len(points), start, interval * MICROSECOND, values)


def csv_metrics(name: str, content: str) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    reader = csv.reader(io.StringIO(content))
    headers = [column.strip() for column in next(reader, [])]
    index = next((i for i, key in enumerate(headers) if key.lower() in TIMESTAMP_HEADERS), None)
    if index is None:
        return {}
    columns = {i: (PurePosixPath(name).stem if key.lower() == 'value' and len(headers) == 2 else key, key)
               for i, key in enumerate(headers) if i != index and key}
    points: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for row in reader:
        try:
            ts = timestamp(row[index])
        except (ValueError, IndexError):
            continue
        for col, metric in columns.items():
            try:
                value = float(row[col])
            except (ValueError, IndexError):
                continue
            if isfinite(value) and abs(value) <= 1e30:
                points[metric].append((ts, value))
    return dict(points)


def access_metrics(content: str) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    points: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for line in content.splitlines():
        match = COMPACT_HTTP.match(line) or ACCESS_HTTP.search(line)
        if not match:
            continue
        try:
            ts = timestamp(match['ts'])
        except ValueError:
            continue
        points[('http_5xx_rate', 'HTTP status')].append((ts, float(int(match['status']) >= 500)))
        duration = match.groupdict().get('duration')
        if duration is not None and len(duration) <= 16:
            points[('response_time_ms', 'Request duration (ms)')].append((ts, float(duration)))
    return dict(points)


@dataclass(frozen=True)
class Evidence:
    files: list[str]
    sha256: str
    series: list[Series]
    excerpts: list[dict[str, str]]


def archive_entries(archive: zipfile.ZipFile, attachments: tuple[tuple[str, bytes], ...]) -> dict[str, zipfile.ZipInfo]:
    members = archive.infolist()
    unpacked = sum(info.file_size for info in members) + sum(len(data) for _, data in attachments)
    if len(members) + len(attachments) > MAX_MEMBERS or unpacked > MAX_UNPACKED_BYTES:
        raise ValueError('The investigation exceeds 256 files or 64 MB unpacked.')
    files = {}
    for info in members:
        name = info.filename
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or len(name) > 240
                or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1):
            raise ValueError('Use regular files with relative paths in an unencrypted ZIP.')
        if info.is_dir():
            continue
        if path.as_posix() != name or name in files:
            raise ValueError('The ZIP has duplicate or noncanonical file paths.')
        files[name] = info
    return files


def read_evidence(payload: bytes, filename: str = 'incident.zip',
                  attachments: tuple[tuple[str, bytes], ...] = (), *,
                  sampling_interval: timedelta | None = None) -> Evidence:
    series: list[Series] = []
    excerpts: list[dict[str, str]] = []
    excerpt_budget = 4800
    digest = hashlib.sha256(payload)
    for name, data in attachments:
        encoded_name = name.encode()
        digest.update(len(encoded_name).to_bytes(2, 'big'))
        digest.update(encoded_name)
        digest.update(len(data).to_bytes(4, 'big'))
        digest.update(data)

    source = zipfile.ZipFile(io.BytesIO(payload)) if filename.lower().endswith('.zip') else nullcontext()
    with source as archive:
        files = archive_entries(archive, attachments) if archive is not None else {}
        manifest = None
        if MANIFEST_NAME in files:
            manifest = json.loads(archive.read(MANIFEST_NAME))
            if not isinstance(manifest, dict) or manifest.get('version') != ARCHIVE_VERSION:
                raise ValueError('The Sentinel archive manifest version is unsupported.')
            manifest = manifest.get('files')
            if not isinstance(manifest, dict) or set(manifest) != (files.keys() - {MANIFEST_NAME}):
                raise ValueError('The Sentinel archive manifest does not match the ZIP contents.')
        uploaded = {} if archive is not None else {filename: payload}
        for index, (name, data) in enumerate(attachments, start=1):
            path = f'attachments/{index}/{name}'
            while path in files or path in uploaded:
                path = f'attachments/{path}'
            uploaded[path] = data
        evidence_files: list[str] = []
        ordered = sorted((files.keys() - {MANIFEST_NAME}) | uploaded.keys(),
                         key=lambda item: (PurePosixPath(item).suffix.lower() != '.csv', item))
        for name in ordered:
            if name in uploaded:
                data = uploaded[name]
            else:
                data = archive.read(name)
                if manifest is not None and hashlib.sha256(data).hexdigest() != manifest[name]:
                    raise ValueError('A file does not match its Sentinel archive checksum.')
            suffix = PurePosixPath(name).suffix.lower()
            if suffix not in EVIDENCE_EXTENSIONS:
                continue
            evidence_files.append(name)
            content = data.decode('utf-8-sig', errors='replace')
            metrics = csv_metrics(name, content) if suffix == '.csv' else access_metrics(content) if suffix == '.log' else {}
            for (metric, source_field), points in metrics.items():
                if len(series) >= 128:
                    raise ValueError('The archive contains more than 128 measurement series.')
                series.append(regularize(f'm{len(series) + 1}', metric[:100], name, source_field[:100], points,
                                         sampling_interval=sampling_interval if name == filename else None))
            if not metrics and suffix in TEXT_EXTENSIONS and excerpt_budget > 0 and name != 'records.jsonl':
                # Numeric exports and recognized access logs already have structured evidence.
                excerpt = regex_pii_filter(content[:min(800, excerpt_budget)])
                excerpts.append({'file': name, 'text': excerpt})
                excerpt_budget -= len(excerpt)
        if not series and all(PurePosixPath(name).stem.lower() == 'readme' for name in evidence_files):
            if any(PurePosixPath(name).suffix.lower() == '.zip' for name in files):
                raise ValueError('The ZIP contains separate incident ZIPs. Extract it and upload one incident ZIP at a time.')
        if not evidence_files:
            raise ValueError('Add a timestamped CSV, access log, or text incident report.')
    return Evidence(evidence_files, digest.hexdigest(), series, excerpts)


def sample_archive(work: Path) -> bytes:
    source = work / 'raw' / 'hf-nginx-logs'
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name in ('access.log', 'metrics/access.csv'):
            output.write(source / name, f'raw/hf-nginx-logs/{name}')
        output.writestr('README.md', 'Safestral example from mindweave/web-server-logs on Hugging Face.\n'
                        'Dataset: https://huggingface.co/datasets/mindweave/web-server-logs\n'
                        'License: https://creativecommons.org/licenses/by-nc/4.0/\n'
                        'This archive contains the public subset in Mistraton work/ and its CSV metric export.\n')
    return archive.getvalue()
