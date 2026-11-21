"""CLI entry points: ingest, extract-metrics, label-windows, build-dataset,
train-detection, train-embedding-head, train-zero-shot, evaluate, forecast,
serve, shadow-run, finetune-timestral.

Run from the backend/ directory:  python -m sentinel_loop <command> [options]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Settings, artifacts_dir_for, db_path_for, resolve_config

logger = logging.getLogger("sentinel_loop")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data-dir", default=None, help="pipeline data dir (default: $SENTINEL_DATA_DIR or ./data)")
    p.add_argument("--config", default=None, help="metrics.yaml path (default: <data-dir>/metrics.yaml if present)")


def _data_dir(args) -> Path:
    return Path(args.data_dir or Settings().data_dir)


def _clients(settings: Settings):
    from .mistral_client import CostLog, MistralClients

    cost_log = CostLog(path=settings.cost_log_path or str(_data_dir_from_settings(settings) / "cost_log.jsonl"))
    return MistralClients(
        api_key=settings.mistral_api_key,
        large_model=settings.large_model,
        shieldstral_model=settings.shieldstral_model,
        cost_log=cost_log,
    )


def _data_dir_from_settings(settings: Settings) -> Path:
    return Path(settings.data_dir)


def cmd_ingest(args) -> int:
    from .ingest.loader import ingest_directory

    data_dir = _data_dir(args)
    records = ingest_directory(data_dir / "raw", data_dir)
    print(f"ingested {len(records)} incident records -> {data_dir / 'records.jsonl'}")
    for r in records:
        print(f"  - {r.incident_id} [{r.timestamp_start.isoformat()} .. {r.timestamp_end.isoformat()}] "
              f"metrics={len(r.metric_sources)}")
    return 0


def cmd_extract_metrics(args) -> int:
    from .extract_metrics.extractor import extract_all

    data_dir = _data_dir(args)
    config, _ = resolve_config(data_dir, args.config)
    clients = _clients(Settings())
    outputs = extract_all(data_dir, clients, config)
    print(f"extracted metrics for {len(outputs)} incidents -> {data_dir / 'metrics.yaml'}")
    for o in outputs:
        print(f"  - {o.incident_id}: {len(o.metric_definitions)} metric definitions, "
              f"{len(o.precursor_signals)} precursor signals")
    return 0


def cmd_label_windows(args) -> int:
    from .labeling.labeler import label_all_windows

    data_dir = _data_dir(args)
    config, _ = resolve_config(data_dir, args.config)
    settings = Settings()
    clients = _clients(settings) if settings.mistral_api_key else None
    windows = label_all_windows(data_dir, db_path_for(settings), config, clients)
    n_val = sum(1 for w in windows if w.validated)
    print(f"labeled {len(windows)} windows ({n_val} shieldstral-validated) -> {db_path_for(settings)}")
    return 0


def cmd_build_dataset(args) -> int:
    from .dataset.assemble import build_dataset

    data_dir = _data_dir(args)
    config, _ = resolve_config(data_dir, args.config)
    res = build_dataset(data_dir, config)
    print(f"dataset: {res['n_detection']} detection windows, {res['n_forecast']} forecasting windows")
    print(f"  splits: {json.loads(Path(res['splits']).read_text())}")
    return 0


def _train(args, strategy: str) -> int:
    from .tuning.train import train

    data_dir = _data_dir(args)
    config, _ = resolve_config(data_dir, args.config)
    det = train(data_dir, config, strategy=strategy, artifacts_dir=artifacts_dir_for(Settings()))
    print(f"trained {strategy} detector -> {getattr(det, 'artifact_path', '')}")
    return 0


def cmd_train_detection(args) -> int:
    return _train(args, "forecasting")


def cmd_train_embedding_head(args) -> int:
    return _train(args, "embedding")


def cmd_train_zeroshot(args) -> int:
    return _train(args, "zero-shot")


def cmd_forecast(args) -> int:
    from .tuning.forecast import forecast_report
    from .tuning.strategies import load_detector

    data_dir = _data_dir(args)
    config, _ = resolve_config(data_dir, args.config)
    art = data_dir / "artifacts" / f"detector_{args.strategy}.json"
    det = load_detector(art)
    report = forecast_report(data_dir, config, det, steps=args.steps)
    print(json.dumps(report, indent=2))
    return 0


def cmd_evaluate(args) -> int:
    from .tuning.evaluate import evaluate_strategy, save_evaluation
    from .tuning.strategies import load_detector

    data_dir = _data_dir(args)
    config, _ = resolve_config(data_dir, args.config)
    art = Path(artifacts_dir_for(Settings())) / f"detector_{args.strategy}.json"
    det = load_detector(art)
    report = evaluate_strategy(det, data_dir, config, split=args.split)
    out = save_evaluation(report, Path(artifacts_dir_for(Settings())) / f"eval_{args.strategy}.json")
    print(report.model_dump_json(indent=2))
    print(f"saved -> {out}")
    return 0


def cmd_finetune_timestral(args) -> int:
    from .tuning.finetune import finetune

    data_dir = _data_dir(args)
    out = finetune(
        data_dir,
        out_dir=args.out or str(Path(artifacts_dir_for(Settings())) / "timestral-hf"),
        base_model=args.base_model,
        epochs=args.epochs,
        max_samples=args.max_samples,
        lora_r=args.lora_r,
    )
    print(f"fine-tuned Timestral -> {out}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .serve.watcher import create_app

    settings = Settings()
    app = create_app(settings=settings)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_shadow_run(args) -> int:
    """Shadow mode: run the detector over the historical test streams, log
    alerts without sending, and report precision on reviewed alerts."""
    from .dataset.assemble import load_splits, load_windows
    from .labeling.store import LabelingStore
    from .tuning.evaluate import evaluate_strategy
    from .tuning.strategies import load_detector

    data_dir = _data_dir(args)
    config, _ = resolve_config(data_dir, args.config)
    settings = Settings()
    det = load_detector(Path(artifacts_dir_for(settings)) / f"detector_{args.strategy}.json")
    report = evaluate_strategy(det, data_dir, config, split="test")
    store = LabelingStore(db_path_for(settings))
    precision, n = store.reviewed_precision()
    print(json.dumps({
        "shadow_run": report.model_dump(mode="json"),
        "reviewed_alerts": n,
        "reviewed_precision": precision,
        "shadow_precision_threshold": config.thresholds.shadow_precision_threshold,
        "passes_threshold": precision >= config.thresholds.shadow_precision_threshold,
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel_loop", description="Sentinel-Loop incident detection pipeline")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="ingest raw incident bundles -> records.jsonl")
    _add_common(p)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("extract-metrics", help="Mistral Large metric extraction -> metrics.yaml")
    _add_common(p)
    p.set_defaults(func=cmd_extract_metrics)

    p = sub.add_parser("label-windows", help="Shieldstral window labeling -> labeling store")
    _add_common(p)
    p.set_defaults(func=cmd_label_windows)

    p = sub.add_parser("build-dataset", help="assemble detection/forecasting sets + splits")
    _add_common(p)
    p.set_defaults(func=cmd_build_dataset)

    p = sub.add_parser("train-detection", help="train forecasting-based Timestral detector (primary)")
    _add_common(p)
    p.set_defaults(func=cmd_train_detection)

    p = sub.add_parser("train-embedding-head", help="train embedding-based classifier head")
    _add_common(p)
    p.set_defaults(func=cmd_train_embedding_head)

    p = sub.add_parser("train-zero-shot", help="zero-shot baseline (control, no tuning)")
    _add_common(p)
    p.set_defaults(func=cmd_train_zeroshot)

    p = sub.add_parser("forecast", help="multi-step issue forecast from a trained detector")
    _add_common(p)
    p.add_argument("--strategy", default="forecasting", choices=["forecasting", "embedding", "zero-shot"])
    p.add_argument("--steps", type=int, default=12, help="forecast horizon in sampling steps")
    p.set_defaults(func=cmd_forecast)

    p = sub.add_parser("evaluate", help="evaluate a strategy on the held-out incident test set")
    _add_common(p)
    p.add_argument("--strategy", default="forecasting", choices=["forecasting", "embedding", "zero-shot"])
    p.add_argument("--split", default="test")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("serve", help="run the FastAPI watcher (self-hosted Timestral)")
    _add_common(p)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8001)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("shadow-run", help="shadow mode over historical test streams")
    _add_common(p)
    p.add_argument("--strategy", default="forecasting", choices=["forecasting", "embedding", "zero-shot"])
    p.set_defaults(func=cmd_shadow_run)

    p = sub.add_parser("finetune-timestral", help="QLoRA fine-tune a small open-weights checkpoint as Timestral (needs GPU + requirements-colab.txt)")
    _add_common(p)
    p.add_argument("--base-model", default=None, help="HF base model (default: $TIMESTRAL_BASE_MODEL or SmolLM2-135M-Instruct)")
    p.add_argument("--out", default=None, help="output dir (default: <artifacts>/timestral-hf)")
    p.add_argument("--epochs", type=float, default=1.0)
    p.add_argument("--max-samples", type=int, default=None)
    p.add_argument("--lora-r", type=int, default=16)
    p.set_defaults(func=cmd_finetune_timestral)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
