"""Explicit whole-story shadow migration and safe rollback; never runs on startup.

All snapshots belong under the supplied private data root. The CLI requires an
explicit root and story; it never resolves the user's configured production root.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from functools import wraps
from contextlib import closing
from pathlib import Path

from mrp.storage.atomic import write_json_atomic
from mrp.storage.json_store import FileStamp
from mrp.storage.paths import AppPaths, is_safe_name
from mrp.storage.save_repo import SaveRepo, summary_from_save
from mrp.storage.session_repo import SessionRepo, summary_from_state
from mrp.storage.story_sqlite import canonical, digest
from mrp.storage.data_lease import DataRootLease
from mrp.orchestrator.migration import migrate_state_to_v3_dict, migrate_save_to_v3_dict
from mrp.shared.models import SessionState, SaveFile
from mrp.storage.command_receipts import legacy_document, unwrap_legacy_document


def exclusive_maintenance(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        if self._lease_depth:
            return method(self, *args, **kwargs)
        with DataRootLease(self.root):
            self._lease_depth += 1
            try:
                return method(self, *args, **kwargs)
            finally:
                self._lease_depth -= 1
    return locked


class StoryMigration:
    def __init__(self, data_root: Path):
        self.root = Path(data_root).resolve()
        self._lease_depth = 0
        with DataRootLease(self.root):
            self.repo = SessionRepo(AppPaths(self.root))
            self.saves = SaveRepo(AppPaths(self.root))
        self.db = self.repo.story_db

    @exclusive_maintenance
    def prepare(self, story_id: str):
        if not is_safe_name(story_id):
            raise ValueError('Invalid story identity')
        rows = [row for row in self.repo.list_summaries_sync() if (row.story_id or row.id) == story_id]
        if not rows or any(self.db.owns(row.id) for row in rows):
            raise ValueError('Migration requires one complete legacy JSON story')
        migration_id = 'migration-' + str(time.time_ns())
        folder = self.root / 'migrations' / migration_id
        folder.mkdir(parents=True, exist_ok=False)
        raw_sources = {row.id: self.repo.path_for(row.id).read_bytes() for row in rows}
        states = {branch: SessionState.model_validate(migrate_state_to_v3_dict(unwrap_legacy_document(json.loads(raw)))[0]) for branch, raw in raw_sources.items()}
        if any(value is None for value in states.values()):
            raise ValueError('Unreadable source branch')
        if any(state.meta.parent_branch_id and state.meta.parent_branch_id not in states for state in states.values()):
            raise ValueError('Story branch closure is incomplete')
        source = {}
        for branch, state in states.items():
            path = self.repo.path_for(branch)
            data = raw_sources[branch]
            (folder / path.name).write_bytes(data)
            source[branch] = {'sha256': hashlib.sha256(data).hexdigest(), 'state_hash': digest(state.model_dump(mode='json'))}
        save_ids = []
        for row in self.saves.list_sync():
            if row.session_id in states:
                path = self.saves.path_for(row.id)
                raw = path.read_bytes()
                (folder / path.name).write_bytes(raw)
                save_ids.append({'id': row.id, 'sha256': hashlib.sha256(raw).hexdigest()})
        # SQLite's backup API includes committed WAL pages. Keep this private;
        # rollback never overwrites this DB (other stories may have advanced).
        with closing(self.db.connect()) as connection, closing(sqlite3.connect(folder / 'memory-consistent.sqlite')) as backup:
            connection.backup(backup)
        manifest = {'format': 1, 'id': migration_id, 'story_id': story_id,
                    'branches': source, 'saves': save_ids, 'source_retained': True}
        write_json_atomic(folder / 'manifest.json', manifest, fsync=True)
        with self.db.transaction() as connection:
            for branch, state in states.items():
                summary = summary_from_state(state, FileStamp.of(self.repo.path_for(branch)))
                self.db.write(state, summary.model_dump(mode='json'), active=False,
                              source_hash=source[branch]['sha256'], db=connection)
            for item in save_ids:
                save = SaveFile.model_validate(migrate_save_to_v3_dict(json.loads((folder / f"{item['id']}.json").read_bytes()))[0])
                summary = summary_from_save(item['id'], save, FileStamp.of(self.saves.path_for(item['id'])))
                self.db.put_save(item['id'], save, summary.model_dump(mode='json'), active=False, db=connection)
            connection.execute('INSERT INTO migrations VALUES(?,?,?,?)', (migration_id, story_id, 'shadow', canonical(manifest)))
        self.validate(migration_id)
        return manifest

    def _manifest(self, migration_id):
        if not is_safe_name(migration_id):
            raise ValueError('Invalid migration identity')
        with self.db.connect() as connection:
            row = connection.execute('SELECT status,manifest FROM migrations WHERE id=?', (migration_id,)).fetchone()
            if not row:
                raise ValueError('Unknown migration')
            manifest=json.loads(row[1])
            if any(connection.execute('SELECT 1 FROM branch_purges WHERE branch_id=?',(branch,)).fetchone() for branch in manifest['branches']):
                raise ValueError('Purged story identities cannot be revived by a historical migration bundle')
            return row[0], manifest

    @exclusive_maintenance
    def validate(self, migration_id):
        status, manifest = self._manifest(migration_id)
        if status not in {'shadow', 'validated'}:
            raise ValueError('Only an inactive migration shadow can be validated')
        for branch, identity in manifest['branches'].items():
            path = self.repo.path_for(branch)
            if hashlib.sha256(path.read_bytes()).hexdigest() != identity['sha256']:
                raise ValueError('Source changed since shadow preparation; rebuild shadow')
            state = self.db.load(branch, active_only=False)
            if state is None or digest(state.model_dump(mode='json')) != identity['state_hash']:
                raise ValueError('Shadow does not roundtrip exactly')
        for save in manifest['saves']:
            if hashlib.sha256(self.saves.path_for(save['id']).read_bytes()).hexdigest() != save['sha256']:
                raise ValueError('Source save changed')
        with self.db.transaction() as connection:
            connection.execute("UPDATE migrations SET status='validated' WHERE id=?", (migration_id,))
        return {'validated': True, 'branches': len(manifest['branches']), 'saves': len(manifest['saves'])}

    @exclusive_maintenance
    def activate(self, migration_id):
        self.validate(migration_id)
        _, manifest = self._manifest(migration_id)
        with self.db.transaction() as connection:
            # Recheck all identities inside the write fence; JSON writers must
            # be quiescent for this explicit maintenance operation.
            for branch, identity in manifest['branches'].items():
                if hashlib.sha256(self.repo.path_for(branch).read_bytes()).hexdigest() != identity['sha256']:
                    raise ValueError('Source changed before activation')
            for branch in manifest['branches']:
                connection.execute('UPDATE branches SET active=1 WHERE id=? AND active=0', (branch,))
            for save in manifest['saves']:
                connection.execute('UPDATE story_saves SET active=1 WHERE id=?', (save['id'],))
            connection.execute("UPDATE migrations SET status='activating' WHERE id=?", (migration_id,))
        # The immutable exact original lives in migrations/<id>. These markers
        # reject old readers instead of letting them load a stale writable head.
        for branch in manifest['branches']:
            write_json_atomic(self.repo.path_for(branch), {'schema_version': 4, 'storage': 'sqlite',
                              'minimum_reader': 3, 'migration_id': migration_id, 'branch_id': branch}, fsync=True)
        with self.db.transaction() as connection:
            connection.execute("UPDATE migrations SET status='active' WHERE id=?", (migration_id,))
        return {'active': True, 'migration_id': migration_id}

    @exclusive_maintenance
    def rollback(self, migration_id):
        status, manifest = self._manifest(migration_id)
        if status not in {'active', 'rolling_back'}:
            raise ValueError('Migration is not active')
        with self.db.transaction() as connection:
            connection.execute("UPDATE migrations SET status='rolling_back' WHERE id=?", (migration_id,))
        # Export every current branch of this story, including ones created
        # after migration. Never replace with the frozen pre-migration snapshot.
        with self.db.transaction() as connection:
            ids = [row[0] for row in connection.execute('SELECT id FROM branches WHERE story_id=? AND active=1', (manifest['story_id'],))]
            for branch in ids:
                state = self.db.load(branch, db=connection)
                write_json_atomic(self.repo.path_for(branch), legacy_document(state), indent=None, fsync=True)
            saves = connection.execute('SELECT id,material_hash FROM story_saves WHERE branch_id IN (SELECT id FROM branches WHERE story_id=?) AND active=1', (manifest['story_id'],)).fetchall()
            for save_id, material in saves:
                raw = self.db._decode(connection, {'__mrp_material_v1__': material})
                write_json_atomic(self.saves.path_for(save_id), raw, indent=None, fsync=True)
            for branch in ids:
                connection.execute('UPDATE branches SET active=0 WHERE id=?', (branch,))
            for save_id, _ in saves:
                connection.execute('UPDATE story_saves SET active=0 WHERE id=?', (save_id,))
            connection.execute("UPDATE migrations SET status='rolled_back' WHERE id=?", (migration_id,))
        return {'rolled_back': True, 'exported_current_branches': len(ids)}

    @exclusive_maintenance
    def recover(self, migration_id):
        status, manifest = self._manifest(migration_id)
        if status == 'activating':
            for branch in manifest['branches']:
                write_json_atomic(self.repo.path_for(branch), {'schema_version': 4, 'storage': 'sqlite',
                                  'minimum_reader': 3, 'migration_id': migration_id, 'branch_id': branch}, fsync=True)
            with self.db.transaction() as connection:
                connection.execute("UPDATE migrations SET status='active' WHERE id=?", (migration_id,))
            return {'recovered': True, 'status': 'active'}
        if status == 'rolling_back':
            return self.rollback(migration_id)
        return {'recovered': False, 'status': status}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('action', choices=['prepare', 'validate', 'activate', 'rollback', 'recover'])
    parser.add_argument('identity')
    args = parser.parse_args()
    migration = StoryMigration(args.data_root)
    print(json.dumps(getattr(migration, args.action)(args.identity), ensure_ascii=False))


if __name__ == '__main__':
    main()
