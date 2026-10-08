"""Composition adapters for lifecycle repository, runtime and external effects."""
import logging
from contextlib import asynccontextmanager
from mrp.application.lifecycle_ports import LifecyclePorts, CreationPorts

logger=logging.getLogger('mrp.lifecycle')

def lifecycle_ports(container):
    repo=container.sessions.create_lifecycle_repo(container.saves)
    def reject_busy(story):
        for runner in container.runners.values():
            if (runner.state.meta.story_id or runner.state.meta.id)==story and (runner.busy() or runner.runtime.consolidation_lock.locked()):
                raise repo.conflict('故事仍有生成或记忆整理任务，请先停止再操作','busy')
    def preset(identity):
        value=container.prompt_presets.get(identity)
        return value.model_dump(mode='json') if value else None
    async def post_commit(action,story,result):
        errors=[]
        for identity in result.get('branch_ids',[]):
            runner=container.runners.pop(identity,None)
            if runner:
                try: await runner.aclose()
                except Exception as exc:
                    errors.append(exc); logger.warning('Committed story runtime close deferred',exc_info=True)
            try: container.story_search.delete_branch(identity)
            except Exception as exc:
                errors.append(exc); logger.warning('Committed story index cleanup deferred',exc_info=True)
        if action=='preset':
            for runner in list(container.runners.values()):
                if (runner.state.meta.story_id or runner.state.meta.id)==story:
                    try:
                        state=await container.sessions.load_state_readonly(runner.state.meta.id)
                        runner.state=state; runner._committed_state=state.model_copy(deep=True)
                    except Exception as exc: errors.append(exc)
        if errors: raise RuntimeError('Committed lifecycle effects deferred: '+','.join(type(error).__name__ for error in errors))
    async def adopt(story):
        await container.story_backups.adopt_legacy_trash(story,repo)
    def diagnostic(story,error):
        container.story_backups.errors[story]='生命周期正文已提交，派生清理待重试（'+type(error).__name__+'）'
        logger.warning('Committed lifecycle delivery deferred for %s',story,exc_info=error)
    return LifecyclePorts(repo,container.sessions.require_visible_branch,container.sessions.story_db.active,
        container.sessions.load_state_readonly,reject_busy,container.story_backups.snapshot,preset,post_commit,
        lambda:repo.cleanup_purges(container),adopt,diagnostic)

def creation_ports(container):
    @asynccontextmanager
    async def publication_gate(action,payload):
        service=container.story_lifecycle
        branch=payload.get('branch_id')
        if not branch and action=='save.fork':
            save=await container.saves.load(payload['save_id'])
            branch=save.state.meta.id if save else None
        if branch:
            service.reject_busy(service.repo.story_for(branch))
            async with service.branch_gate(branch):
                await service.ensure_transactional_branch(branch)
                yield
        else: yield
    async def execute(action,payload,operation,create,response_meta,gate):
        return await container.sessions.execute_creation(container.memory_store,action,payload,operation,create,response_meta,gate)
    return CreationPorts(execute,publication_gate)
