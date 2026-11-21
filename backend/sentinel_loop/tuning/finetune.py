"""Fine-tune a small open-weights checkpoint as the self-hosted Timestral detector.

Builds a chat-style JSONL from the TRAIN split of the detection dataset
(dataset/windows.jsonl + dataset/splits.json) and QLoRA fine-tunes a small base
model (default SmolLM2-135M-Instruct, override with --base-model /
TIMESTRAL_BASE_MODEL) using transformers + peft + trl. The merged model is saved
to <artifacts>/timestral-hf and served on Colab's GPU via
`POST /sentinel/timestral/chat` (see backend/colab_app.py).

Heavy deps (torch/transformers/peft/trl) are imported lazily inside finetune()
so the rest of the pipeline runs without them: pip install -r requirements-colab.txt

Only Timestral is tuned, and only on the train split — the held-out incident
test set is never touched here (same rule as tuning/train.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..dataset.assemble import load_splits, load_windows

TIMESTRAL_SYSTEM = (
    "You are Timestral, a time-series security analyst. You receive metric windows "
    "as JSON and answer with a single JSON object: {\"window_state\": one of "
    "normal|precursor|incident|degraded, \"confidence\": 0..1, \"lead_time_minutes\": number, "
    "\"explanation\": short text}."
)

DEFAULT_BASE_MODEL = "HuggingFaceTB/SmolLM2-135M-Instruct"


def build_chat_dataset(data_dir: str | Path, out_jsonl: str | Path, max_samples: int | None = None) -> int:
    """Turn labeled train windows into chat samples. Returns sample count."""
    data_dir = Path(data_dir)
    splits = load_splits(data_dir / "dataset" / "splits.json")
    train_ids = set(splits["train"])
    windows = [w for w in load_windows(data_dir / "dataset" / "windows.jsonl") if w.incident_id in train_ids]
    if max_samples:
        windows = windows[:max_samples]
    out_jsonl = Path(out_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for w in windows:
            user = json.dumps({
                "metric": w.metric,
                "window_start": w.window_start.isoformat(),
                "window_end": w.window_end.isoformat(),
                "values": w.values,
            })
            assistant = json.dumps({
                "window_state": w.label.value,
                "incident_id": w.incident_id,
                "explanation": f"window labeled {w.label.value} for incident {w.incident_id}",
            })
            fh.write(json.dumps({"messages": [
                {"role": "system", "content": TIMESTRAL_SYSTEM},
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]}) + "\n")
            n += 1
    return n


def finetune(
    data_dir: str | Path,
    out_dir: str | Path | None = None,
    base_model: str | None = None,
    epochs: float = 1.0,
    max_samples: int | None = None,
    learning_rate: float = 2e-4,
    lora_r: int = 16,
    max_seq_length: int = 1024,
) -> Path:
    """QLoRA fine-tune; returns the directory with the merged model."""
    data_dir = Path(data_dir)
    out_dir = Path(out_dir or data_dir / "artifacts" / "timestral-hf")
    base_model = base_model or os.environ.get("TIMESTRAL_BASE_MODEL", DEFAULT_BASE_MODEL)

    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    jsonl = data_dir / "artifacts" / "timestral_train.jsonl"
    n_samples = build_chat_dataset(data_dir, jsonl, max_samples=max_samples)
    if n_samples == 0:
        raise RuntimeError("no training windows; run build-dataset first")
    dataset = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]

    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    use_4bit = torch.cuda.is_available()
    model_kwargs = {}
    if use_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True, bnb_4bit_quant_type="nf4")
        model_kwargs["device_map"] = "auto"
    model = AutoModelForCausalLM.from_pretrained(base_model, **model_kwargs)

    peft_config = LoraConfig(
        r=lora_r, lora_alpha=2 * lora_r, lora_dropout=0.05,
        target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM",
    )
    trainer = SFTTrainer(
        model=model,
        args=SFTConfig(
            output_dir=str(out_dir) + "-trainer",
            num_train_epochs=epochs,
            per_device_train_batch_size=4,
            gradient_accumulation_steps=4,
            learning_rate=learning_rate,
            logging_steps=10,
            save_strategy="no",
            report_to=[],
            max_seq_length=max_seq_length,
            packing=False,
        ),
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
    )
    trainer.train()

    merged = trainer.model
    if hasattr(merged, "merge_and_unload"):
        merged = merged.merge_and_unload()
    out_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))

    log = {
        "base_model": base_model,
        "epochs": epochs,
        "samples": n_samples,
        "lora_r": lora_r,
        "4bit": use_4bit,
        "loss_history": [h for h in trainer.state.log_history if "loss" in h],
        "out_dir": str(out_dir),
    }
    (out_dir / "training_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"fine-tuned Timestral ({n_samples} samples from {base_model}) -> {out_dir}")
    return out_dir
