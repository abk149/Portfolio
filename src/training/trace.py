"""Save every (Input → Agent Thought → Outcome) tuple to JSONL.

Output is ready to feed MLX-LM LoRA later:

    {"prompt": "<input + tool log>", "completion": "<think>...</think> <answer>"}

Each run writes to its own file under settings.trace_dir so you can curate by
outcome (label later as gold/bad) before fine-tuning.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import settings


_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)


class TraceRecorder:
    def __init__(self, run_id: Optional[str] = None):
        settings.trace_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.path = settings.trace_dir / f"{datetime.now():%Y%m%d}_{self.run_id}.jsonl"
        self.events: list[dict] = []

    def record(self, kind: str, **fields: Any) -> None:
        evt = {"ts": datetime.now(timezone.utc).isoformat(),
               "run_id": self.run_id, "kind": kind, **fields}
        self.events.append(evt)
        with self.path.open("a") as fh:
            fh.write(json.dumps(evt, default=str) + "\n")

    def record_step(self, prompt: str, response: str, outcome: Optional[str] = None) -> None:
        thinks = _THINK_RE.findall(response)
        answer = _THINK_RE.sub("", response).strip()
        self.record("step",
                    prompt=prompt[:8000],
                    think="\n---\n".join(thinks)[:8000],
                    answer=answer[:8000],
                    outcome=outcome)

    def to_lora_jsonl(self, out_path: Optional[Path] = None,
                      only_gold: bool = True) -> Path:
        """Re-emit as MLX-LM LoRA training format (prompt/completion pairs)."""
        out_path = out_path or (settings.trace_dir / f"lora_{self.run_id}.jsonl")
        with self.path.open() as src, out_path.open("w") as dst:
            for line in src:
                evt = json.loads(line)
                if evt.get("kind") != "step":
                    continue
                if only_gold and evt.get("outcome") != "gold":
                    continue
                completion = (f"<think>{evt.get('think', '')}</think> "
                              f"{evt.get('answer', '')}").strip()
                dst.write(json.dumps({"prompt": evt.get("prompt", ""),
                                      "completion": completion}) + "\n")
        return out_path
