"""Restore reporting projections from durable receipts, including auxiliary calls."""
from mrp.shared.models import UsageRecord

FIELDS = ("input_tokens", "output_tokens", "cached_tokens")
ALIASES = {"group_response", "proactive", "continue"}


def add_bucket(store, key, usage):
    bucket = store.setdefault(key, dict.fromkeys(FIELDS, 0))
    for field in FIELDS:
        bucket[field] += getattr(usage, field)


def restore_usage(runner):
    state = runner.state
    if not state.usage_records and state.messages:
        # Legacy receipts cannot reconstruct failed calls or historical auxiliary
        # usage. Recover observable candidates and explicitly mark the gap.
        state.usage_incomplete = True
        for message in state.messages:
            candidates = [(f"legacy:{message.id}:active", message.generation_meta)]
            if message.variants:
                candidates = [(f"legacy:{message.id}:{v.id}", v.generation_meta) for v in message.variants]
            for key, meta in candidates:
                if meta is not None:
                    state.usage_records.append(UsageRecord(id=meta.generation_id or key,
                        model=meta.model, character_id=message.actor, usage=meta.usage))
    runner.runtime.cost_by_model.clear()
    runner.runtime.cost_by_character.clear()
    runner.runtime.cost_by_purpose.clear()
    seen = set()
    unique = []
    for row in state.usage_records:
        if row.id in seen:
            continue
        seen.add(row.id)
        unique.append(row)
        project_record(runner, row)
    state.usage_records = unique


def project_record(runner, row):
    add_bucket(runner.runtime.cost_by_model, row.model or "unknown", row.usage)
    if row.character_id:
        add_bucket(runner.runtime.cost_by_character, row.character_id, row.usage)
    if row.purpose != "generation":
        add_bucket(runner.runtime.cost_by_purpose, row.purpose, row.usage)
    for label in row.labels:
        add_bucket(runner.runtime.cost_by_purpose, label, row.usage)


def record_usage(runner, purpose, usage, *, model="", character_id=None, ctx=None):
    record = UsageRecord(purpose=purpose, model=model, character_id=character_id, usage=usage,
        generation_id=getattr(ctx, "generation_id", None), operation_id=getattr(ctx, "operation_id", None),
        attempt_id=getattr(ctx, "attempt_id", None))
    recorder = getattr(runner, "usage_recorder", None)
    if recorder is not None:
        recorder(runner.state.meta.id, record)
    runner.state.usage_records.append(record)
    project_record(runner, record)
    return record


def label_main_call(runner, label, *, generation_id=None):
    if generation_id:
        rows = [row for row in runner.state.usage_records
                if row.purpose == "generation" and row.generation_id == generation_id]
    else:
        latest = next((row for row in reversed(runner.state.usage_records) if row.purpose == "generation"), None)
        rows = [latest] if latest else []
    for row in rows:
        if label in row.labels:
            continue
        row.labels.append(label)
        recorder = getattr(runner, "usage_recorder", None)
        if recorder:
            recorder(runner.state.meta.id, row)
        add_bucket(runner.runtime.cost_by_purpose, label, row.usage)
