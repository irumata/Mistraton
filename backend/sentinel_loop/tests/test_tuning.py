import pytest

from sentinel_loop.config import resolve_config
from sentinel_loop.dataset.assemble import build_dataset
from sentinel_loop.extract_metrics.extractor import extract_all
from sentinel_loop.schemas import WindowState
from sentinel_loop.tuning.evaluate import evaluate_strategy
from sentinel_loop.tuning.strategies import load_detector, save_detector
from sentinel_loop.tuning.train import train


@pytest.fixture
def prepared(data_dir, config, mock_clients):
    """ingest (done in data_dir fixture) -> extract (mocked) -> dataset."""
    extract_all(data_dir, mock_clients, config)
    cfg, _ = resolve_config(data_dir, None)  # merged metrics.yaml now present
    build_dataset(data_dir, cfg)
    return cfg


def test_train_forecasting_and_evaluate(data_dir, prepared):
    det = train(data_dir, prepared, strategy="forecasting", artifacts_dir=data_dir / "artifacts")
    assert det.name == "forecasting"
    report = evaluate_strategy(det, data_dir, prepared, split="test")
    assert report.incidents_evaluated, "held-out incident should be detected"
    inc = report.per_class["incident"]
    assert inc.recall > 0
    assert report.mean_lead_time_minutes > 0
    assert report.mean_quantile_loss_normal >= 0


def test_train_embedding_head(data_dir, prepared):
    det = train(data_dir, prepared, strategy="embedding", artifacts_dir=data_dir / "artifacts")
    report = evaluate_strategy(det, data_dir, prepared, split="test")
    assert set(report.per_class) >= {"normal", "precursor", "incident"}


def test_train_zero_shot_control(data_dir, prepared):
    det = train(data_dir, prepared, strategy="zero-shot", artifacts_dir=data_dir / "artifacts")
    report = evaluate_strategy(det, data_dir, prepared, split="test")
    # zero-shot must detect the large spike on the held-out incident too
    assert report.per_class["incident"].recall > 0


def test_detector_serialization_roundtrip(data_dir, prepared, tmp_path):
    det = train(data_dir, prepared, strategy="forecasting", artifacts_dir=data_dir / "artifacts")
    path = save_detector(det, tmp_path / "det.json")
    loaded = load_detector(path)
    assert loaded.name == "forecasting"
    assert loaded.backend.forecast.keys() == det.backend.forecast.keys()


def test_embedding_serialization_roundtrip(data_dir, prepared, tmp_path):
    det = train(data_dir, prepared, strategy="embedding", artifacts_dir=data_dir / "artifacts")
    path = save_detector(det, tmp_path / "det_emb.json")
    loaded = load_detector(path)
    assert loaded.name == "embedding"
    assert loaded.weights is not None


def test_invalid_strategy(data_dir, prepared):
    with pytest.raises(ValueError):
        train(data_dir, prepared, strategy="magic")
