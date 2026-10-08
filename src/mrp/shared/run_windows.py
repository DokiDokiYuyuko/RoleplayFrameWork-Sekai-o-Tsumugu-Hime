"""Page-associated execution state shared by cached and SQL read projections."""
RESUMABLE_RUN_STATUSES = ('queued','running','paused','awaiting_user','awaiting_director','interrupted','failed')

def field(value,key,default=None):
    return value.get(key,default) if isinstance(value,dict) else getattr(value,key,default)

def run_message_ids(run):
    ids=set()
    for key in ('input_message_ids','seed_message_ids','prepared_scene_message_ids','current_trigger_message_ids','current_reply_to_message_ids'):
        ids.update(field(run,key,[]) or [])
    for key in ('slots','steps'):
        ids.update(field(item,'message_id') for item in (field(run,key,[]) or []))
    ids.update(field(run,key) for key in ('last_committed_message_id','current_message_id'))
    return ids - {None}

def select_page_runs(runs,messages):
    ids={field(message,'id') for message in messages}
    operations={field(message,'operation_id') for message in messages} - {None}
    latest=next((field(run,'id') for run in reversed(runs) if field(run,'status') in RESUMABLE_RUN_STATUSES),None)
    return [run for run in runs if field(run,'id')==latest or field(run,'operation_id') in operations or run_message_ids(run) & ids]
