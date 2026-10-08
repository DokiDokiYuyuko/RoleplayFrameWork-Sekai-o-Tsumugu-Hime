"""Versioned portable representation of immutable committed command results."""
from __future__ import annotations

import json
from copy import deepcopy

RECEIPT_KEY = '__mrp_command_receipts_v1__'
AUDIT_KEY = '__mrp_imported_command_receipts_v1__'
CLAIM_KEY = '__mrp_generation_claims_v1__'


class CommandConflict(ValueError):
    command_conflict = True
    code = 'operation_conflict'


class GenerationIncomplete(CommandConflict):
    code = 'operation_incomplete'


def portable_claims(state):
    collection = state.generation_operations.get(CLAIM_KEY)
    if collection is None:
        return {}
    if not isinstance(collection, dict) or collection.get('format') != 'mrp.generation_claims' or collection.get('version') != 1 or not isinstance(collection.get('entries'), dict):
        raise CommandConflict('生成尝试凭据版本不支持')
    if any(not isinstance(operation, str) or not operation or not isinstance(fingerprint, str) or not fingerprint
           for operation, fingerprint in collection['entries'].items()):
        raise CommandConflict('生成尝试凭据格式无效')
    return collection['entries']


def put_portable_claims(state, entries):
    if entries:
        state.generation_operations[CLAIM_KEY] = {'format':'mrp.generation_claims', 'version':1, 'entries':deepcopy(entries)}
    else:
        state.generation_operations.pop(CLAIM_KEY, None)


def merge_claims(target, source):
    entries = dict(portable_claims(target))
    for operation, fingerprint in portable_claims(source).items():
        if operation in entries and entries[operation] != fingerprint:
            raise CommandConflict('不能改写既有生成尝试身份')
        entries[operation] = fingerprint
    put_portable_claims(target, entries)
    jobs=dict(portable_jobs(target))
    for operation,job in portable_jobs(source).items():
        if operation in jobs and jobs[operation]['fingerprint'] != job['fingerprint']:
            raise CommandConflict('不能改写记忆任务身份')
        jobs[operation]=deepcopy(job)
    put_portable_jobs(target,jobs)


class CommandAlreadyCommitted(CommandConflict):
    def __init__(self, receipt):
        super().__init__('命令已提交；请重放已保存结果')
        self.receipt = deepcopy(receipt)


def validate_receipt(receipt):
    if not isinstance(receipt, dict) or not isinstance(receipt.get('operation_id'), str) or not receipt['operation_id'] or not isinstance(receipt.get('fingerprint'), str) or not receipt['fingerprint'] or 'result' not in receipt:
        raise CommandConflict('命令凭据格式无效')
    try:
        json.dumps(receipt['result'], allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CommandConflict('命令结果不是有效JSON') from exc
    return receipt


def portable_receipts(state):
    collection = state.generation_operations.get(RECEIPT_KEY)
    if collection is None:
        return {}
    if not isinstance(collection, dict) or collection.get('format') != 'mrp.command_receipts' or collection.get('version') != 1 or not isinstance(collection.get('entries'), dict):
        raise CommandConflict('命令凭据版本不支持；拒绝损坏既有结果')
    for operation, receipt in collection['entries'].items():
        validate_receipt({'operation_id': operation, **receipt})
        if not isinstance(receipt.get('revision'), int) or receipt['revision'] < 0:
            raise CommandConflict('命令凭据修订无效')
    return collection['entries']


def put_portable_receipts(state, entries):
    if entries:
        state.generation_operations[RECEIPT_KEY] = {
            'format': 'mrp.command_receipts', 'version': 1, 'entries': deepcopy(entries)}
    else:
        state.generation_operations.pop(RECEIPT_KEY, None)


def public_receipt(receipt):
    result = {'fingerprint': receipt['fingerprint'], 'result': deepcopy(receipt['result'])}
    if 'revision' in receipt:
        result['revision'] = receipt['revision']
    return result


def inherit_receipt_audit(target, source):
    """A new branch can retain provenance, never another branch's command ID."""
    audit = deepcopy(source.generation_operations.get(AUDIT_KEY, {
        'format': 'mrp.imported_command_receipts', 'version': 1,
        'executable': False, 'sources': []}))
    if audit.get('format') != 'mrp.imported_command_receipts' or audit.get('version') != 1 or audit.get('executable') is not False or not isinstance(audit.get('sources'), list):
        raise CommandConflict('导入命令审计版本不支持')
    entries = portable_receipts(source)
    claims = portable_claims(source)
    jobs = portable_jobs(source)
    if entries or claims or jobs:
        provenance = {'source_branch_id': source.meta.id, 'entries': deepcopy(entries), 'generation_claims':deepcopy(claims),'memory_jobs':deepcopy(jobs)}
        if provenance not in audit['sources']:
            audit['sources'].append(provenance)
    target.generation_operations.pop(RECEIPT_KEY, None)
    target.generation_operations.pop(CLAIM_KEY, None)
    target.generation_operations.pop(JOB_KEY,None)
    if audit['sources']:
        target.generation_operations[AUDIT_KEY] = audit


def legacy_document(state):
    raw = state.model_dump(mode='json')
    if portable_receipts(state) or portable_claims(state) or portable_jobs(state):
        return {'schema_version': 4, 'format': 'mrp.session_receipts',
                'storage_version': 4 if portable_jobs(state) else 3 if portable_claims(state) else 2, 'state': raw}
    return state


def unwrap_legacy_document(raw):
    if isinstance(raw, dict) and raw.get('format') == 'mrp.session_receipts':
        if raw.get('schema_version') != 4 or raw.get('storage_version') not in (2, 3, 4) or not isinstance(raw.get('state'), dict):
            raise CommandConflict('会话存储格式不支持')
        return raw['state']
    return raw


JOB_KEY = '__mrp_memory_jobs_v1__'

def portable_jobs(state):
    row=state.generation_operations.get(JOB_KEY)
    if row is None: return {}
    if not isinstance(row,dict) or row.get('format')!='mrp.memory_jobs' or row.get('version')!=1 or not isinstance(row.get('entries'),dict):
        raise CommandConflict('记忆任务凭据版本不支持')
    for operation,job in row['entries'].items():
        if not isinstance(operation,str) or not operation or not isinstance(job,dict) or not isinstance(job.get('fingerprint'),str) or job.get('status') not in ('pending','failed','committed'):
            raise CommandConflict('记忆任务凭据格式无效')
    return row['entries']

def put_portable_jobs(state,entries):
    if entries: state.generation_operations[JOB_KEY]={'format':'mrp.memory_jobs','version':1,'entries':deepcopy(entries)}
    else: state.generation_operations.pop(JOB_KEY,None)
