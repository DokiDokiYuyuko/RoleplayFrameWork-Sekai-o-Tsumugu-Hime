"""Application bridge for durable hidden publication through narrow effects."""
from .lifecycle_ports import CreationPorts

async def execute_creation(ports: CreationPorts,action,payload,operation_id,create,*,response_meta=None):
    return await ports.execute(action,payload,operation_id,create,response_meta,
        lambda:ports.publication_gate(action,payload))
