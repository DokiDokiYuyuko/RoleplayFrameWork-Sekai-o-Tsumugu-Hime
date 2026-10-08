"""HTTP adapter for story lifecycle receipts."""
from fastapi import HTTPException

async def execute_lifecycle(container, action, story_id, payload, operation_id=None, response=None):
    try:
        result,identity=await container.story_lifecycle.execute(action,story_id,payload,operation_id)
    except ValueError as exc:
        raise HTTPException(getattr(exc,'status_code',409),{'code':getattr(exc,'code','lifecycle_conflict'),'message':str(exc)}) from exc
    if response is not None:
        response.headers['X-Command-ID']=identity
        response.headers['X-Branch-Revision']=str(result['branch_revision'])
    return result
