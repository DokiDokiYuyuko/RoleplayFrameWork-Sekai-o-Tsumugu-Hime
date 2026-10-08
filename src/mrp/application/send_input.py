"""Transport-independent identity for every player submission mode."""
import hashlib
import json
from mrp.shared.models import new_id


def submission_identity(request):
    payload = request.model_dump(mode="json") if hasattr(request, "model_dump") else dict(vars(request))
    operation_id = payload.get("operation_id") or payload.get("client_message_id") or new_id("op")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()
    return operation_id, fingerprint
