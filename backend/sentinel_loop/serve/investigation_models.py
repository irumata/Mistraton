"""Model providers for the archive investigation workflow."""

from __future__ import annotations

import json
import os
from functools import partial
from threading import Lock
from time import monotonic

import httpx
import numpy as np

from ..mistral_client import MistralClients

LARGE_MODEL = 'mistral-large-4-0'
REVIEW_MODEL = 'mistral-small-2603'
SHIELD_MODEL = 'mistralai/Shieldstral-1.0-3B'


class InvestigationError(RuntimeError):
    """An error safe to display on the investigation screen."""


class InvestigationModels:
    def __init__(self, api_key: str, timestral_size: str):
        self.large_model = LARGE_MODEL
        self.review_model = REVIEW_MODEL
        self.shield_model = SHIELD_MODEL
        self.timestral_model = f'mistralai/Timestral-{timestral_size}'
        self._api_key = api_key
        self._forecast_lock = Lock()
        self._shield_lock = Lock()
        self._forecaster = None
        self._shield = None

    def _chat_transport(self, model: str, messages: list[dict], response_format: dict | None = None,
                        *, constrain_json: bool, reasoning_effort: str) -> str:
        chunks = []
        finish_reason = None
        deadline = monotonic() + 100
        parameters = {'model': model, 'messages': messages, 'reasoning_effort': reasoning_effort,
                      'temperature': 0.2, 'max_tokens': 3200, 'stream': True}
        if constrain_json and response_format:
            parameters['response_format'] = response_format
        with httpx.stream(
            'POST',
            'https://api.mistral.ai/v1/chat/completions',
            headers={'Authorization': f'Bearer {self._api_key}'},
            json=parameters,
            timeout=httpx.Timeout(35, connect=10),
        ) as response:
            if not response.is_success:
                raise InvestigationError(f'La Plateforme returned HTTP {response.status_code}.')
            for line in response.iter_lines():
                if monotonic() > deadline:
                    raise InvestigationError('La Plateforme exceeded the response time limit.')
                event_type, separator, payload = line.partition(':')
                if not separator or event_type != 'data':
                    continue
                payload = payload.strip()
                if payload == '[DONE]':
                    if finish_reason == 'length':
                        raise InvestigationError('La Plateforme reached the response token limit.')
                    answer = ''.join(chunks).strip()
                    if answer.startswith('```') and answer.endswith('```'):
                        answer = answer.partition('\n')[2][:-3].strip()
                    return answer
                event = json.loads(payload)
                if 'error' in event:
                    raise InvestigationError('La Plateforme ended the model stream with an error.')
                for choice in event.get('choices', []):
                    finish_reason = choice.get('finish_reason') or finish_reason
                    content = choice.get('delta', {}).get('content')
                    if isinstance(content, str):
                        chunks.append(content)
                    elif isinstance(content, list):
                        chunks.extend(chunk['text'] for chunk in content if chunk.get('type') == 'text')
        raise InvestigationError('La Plateforme disconnected before the model response finished.')

    def large_json(self, instructions: str, evidence: dict, *, constrain_json: bool = True) -> dict:
        return self._model_json(self.large_model, instructions, evidence,
                                constrain_json=constrain_json, reasoning_effort='low')

    def review_json(self, instructions: str, evidence: dict) -> dict:
        return self._model_json(self.review_model, instructions, evidence,
                                constrain_json=True, reasoning_effort='none')

    def _model_json(self, model: str, instructions: str, evidence: dict, *,
                    constrain_json: bool, reasoning_effort: str) -> dict:
        if not self._api_key:
            raise InvestigationError('Set MISTRAL_API_KEY in the Colab environment to use Mistral chat models.')
        transport = partial(self._chat_transport, constrain_json=constrain_json, reasoning_effort=reasoning_effort)
        client = MistralClients(large_model=model, transport=transport, max_retries=2)
        try:
            result = json.loads(client.large_extract(instructions, json.dumps(evidence, allow_nan=False)))
        except (RuntimeError, ValueError, TypeError) as exc:
            raise InvestigationError(f'{model} could not return a complete response. Check its API access and retry.') from exc
        if not isinstance(result, dict):
            raise InvestigationError(f'{model} returned an unexpected response.')
        return result

    def forecast(self, contexts: np.ndarray) -> np.ndarray:
        with self._forecast_lock:
            try:
                if self._forecaster is None:
                    from timestral import TimestralForecaster

                    self._forecaster = TimestralForecaster.from_pretrained(self.timestral_model, device='cpu', token=False)
                bands = []
                for start in range(0, len(contexts), 32):
                    forecast = self._forecaster.forecast(contexts[start:start + 32], prediction_length=1)
                    bands.append(np.stack([forecast.quantile(q)[:, 0, 0] for q in (0.05, 0.5, 0.95)], axis=1))
                return np.concatenate(bands)
            except Exception as exc:
                raise InvestigationError('Timestral could not forecast. Check the model cache and the Colab model dependencies.') from exc

    def _load_shield(self):
        token = os.getenv('HF_TOKEN') or os.getenv('HUGGINGFACE_TOKEN') or os.getenv('HUGGINGFACE_HUB_TOKEN')
        if not token:
            raise InvestigationError('Set HUGGINGFACE_TOKEN with read access to Shieldstral in the Colab environment.')
        from transformers import Mistral3ForConditionalGeneration, MistralCommonBackend
        import torch

        if torch.cuda.is_available():
            device = 'cuda'
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        else:
            device = 'cpu'
            dtype = torch.bfloat16
        tokenizer = MistralCommonBackend.from_pretrained(self.shield_model, token=token)
        tokenizer.padding_side = 'left'
        model = Mistral3ForConditionalGeneration.from_pretrained(
            self.shield_model, token=token, device_map=device, dtype=dtype,
        ).eval()
        return model, tokenizer

    def shield(self, instruction: str, question: str, evidence: dict) -> float:
        return self.shield_many(instruction, question, [evidence])[0]

    def shield_many(self, instruction: str, question: str, evidence_batch: list[dict]) -> list[float]:
        with self._shield_lock:
            try:
                import torch

                if self._shield is None:
                    self._shield = self._load_shield()
                model, tokenizer = self._shield
                conversations = [[
                    {'role': 'system', 'content': 'Judge whether the Document meets the requirements based on the Query and '
                     'the Instruction provided. Note that the answer can only be "yes" or "no".'},
                    {'role': 'user', 'content': f'<Instruct>: {instruction}\n\n<Query>: {question}\n\n'
                     f'<Document>: {json.dumps(evidence, allow_nan=False)}'},
                ] for evidence in evidence_batch]
                encoded = tokenizer.apply_chat_template(conversations, padding=True, return_tensors='pt', return_dict=True)
                with torch.inference_mode():
                    inputs = {key: value.to(model.device) if torch.is_tensor(value) else value
                              for key, value in encoded.items()}
                    position_ids = (inputs['attention_mask'].long().cumsum(dim=-1) - 1).clamp_min(0)
                    logits = model(**inputs, position_ids=position_ids, use_cache=False,
                                   logits_to_keep=1).logits[:, -1].float()
                values, indices = logits.topk(64)
                scores = []
                for row_values, row_indices in zip(values.tolist(), indices.tolist(), strict=True):
                    verdicts = {}
                    for logit, index in zip(row_values, row_indices, strict=True):
                        answer = tokenizer.decode([index]).strip().lower().strip('"\'.')
                        if answer in {'yes', 'no'} and answer not in verdicts:
                            verdicts[answer] = logit
                    if not verdicts:
                        raise InvestigationError('Shieldstral did not emit a yes/no verdict.')
                    if len(verdicts) == 1:
                        scores.append(float('yes' in verdicts))
                    else:
                        pair = torch.tensor([verdicts['no'], verdicts['yes']])
                        scores.append(float(pair.softmax(dim=0)[1]))
                return scores
            except InvestigationError:
                raise
            except Exception as exc:
                raise InvestigationError('Shieldstral could not run. Check access to its Hugging Face weights and Colab memory.') from exc
