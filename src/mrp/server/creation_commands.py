"""HTTP receipt headers for durable entity publication commands."""
from mrp.application.creation import execute_creation as execute_application_creation


async def execute_creation(container, action, payload, operation_id, create, *, response=None):
    metadata = {}
    result = await execute_application_creation(container.creation_ports, action, payload, operation_id,
        create, response_meta=metadata)
    if response is not None:
        response.headers['X-Command-ID'] = metadata['operation_id']
        revision = result.get('branch_revision', result.get('meta', {}).get('branch_revision', 0))
        response.headers['X-Branch-Revision'] = str(revision)
    return result
