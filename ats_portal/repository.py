"""SQLite persistence boundary. Parameterized SQL and explicit transactions."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .classifier import attention_indices
from .models import DomainError

_SCHEMA_V4 = '''
CREATE TABLE runs (
    id TEXT PRIMARY KEY, manifest TEXT NOT NULL,
    mode_id TEXT NOT NULL, mode_name TEXT NOT NULL,
    started_at TEXT NOT NULL, started_epoch REAL NOT NULL,
    run_date TEXT NOT NULL, received_at TEXT NOT NULL,
    upload_state TEXT NOT NULL, status TEXT NOT NULL,
    issue_count INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    attention_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX runs_date ON runs(run_date, started_epoch DESC);
CREATE INDEX runs_mode ON runs(mode_id, started_epoch DESC);
CREATE TABLE issues (run_id TEXT NOT NULL REFERENCES runs(id), category TEXT NOT NULL, category_name TEXT NOT NULL);
CREATE INDEX issues_category ON issues(category,run_id);
CREATE TABLE labels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX labels_name ON labels(name);
CREATE TABLE annotations (
    run_id TEXT NOT NULL REFERENCES runs(id),
    result_index INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, result_index)
);
CREATE INDEX annotations_run ON annotations(run_id);
CREATE TABLE annotation_labels (
    run_id TEXT NOT NULL,
    result_index INTEGER NOT NULL,
    label_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    PRIMARY KEY (run_id, result_index, label_id)
);
CREATE TABLE run_meta (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    device TEXT NOT NULL DEFAULT '',
    firmware TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE ai_analyses (
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, kind)
);
PRAGMA user_version=4;
'''


def load_label_seed(path):
    """Read the optional seed label list; failures mean 'start with no labels'."""
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    labels = doc.get('labels', []) if isinstance(doc, dict) else []
    return [{'id': str(x['id']), 'name': str(x['name'])} for x in labels
            if isinstance(x, dict) and x.get('id') and x.get('name')]


class Repository:
    def __init__(self, path, case_sensitive_ids=None, labels=None):
        self.path = path
        self.case_sensitive_ids = os.name != 'nt' if case_sensitive_ids is None else case_sensitive_ids
        self.labels = labels or []
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 4:
                raise RuntimeError('数据库来自更新版本，请升级程序，禁止降级覆盖数据')
            if version == 0:
                db.executescript(_SCHEMA_V4)
            elif version == 1:
                self._migrate_v1_to_v2(db)
                self._migrate_v2_to_v3(db)
                self._migrate_v3_to_v4(db)
            elif version == 2:
                self._migrate_v2_to_v3(db)
                self._migrate_v3_to_v4(db)
            elif version == 3:
                self._migrate_v3_to_v4(db)
            self._seed_labels(db)

    def _migrate_v1_to_v2(self, db):
        db.execute('ALTER TABLE runs ADD COLUMN attention_count INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE TABLE labels (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)')
        db.execute('CREATE UNIQUE INDEX labels_name ON labels(name)')
        db.execute('CREATE TABLE annotations (run_id TEXT NOT NULL REFERENCES runs(id), result_index INTEGER NOT NULL, note TEXT NOT NULL DEFAULT "", author TEXT NOT NULL DEFAULT "", created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (run_id, result_index))')
        db.execute('CREATE INDEX annotations_run ON annotations(run_id)')
        db.execute('CREATE TABLE annotation_labels (run_id TEXT NOT NULL, result_index INTEGER NOT NULL, label_id TEXT NOT NULL, label_name TEXT NOT NULL, PRIMARY KEY (run_id, result_index, label_id))')
        rows = db.execute("SELECT id, payload FROM runs WHERE upload_state='complete'").fetchall()
        for row in rows:
            run = json.loads(row['payload'])
            count = len(attention_indices(run.get('results', []), run.get('issues', [])))
            db.execute('UPDATE runs SET attention_count=? WHERE id=?', (count, row['id']))
        db.execute('PRAGMA user_version=2')

    def _migrate_v2_to_v3(self, db):
        db.execute('CREATE TABLE run_meta (run_id TEXT PRIMARY KEY REFERENCES runs(id), device TEXT NOT NULL DEFAULT "", firmware TEXT NOT NULL DEFAULT "", author TEXT NOT NULL DEFAULT "", updated_at TEXT NOT NULL)')
        db.execute('PRAGMA user_version=3')

    def _migrate_v3_to_v4(self, db):
        db.execute('CREATE TABLE ai_analyses (scope TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (scope, kind))')
        db.execute('PRAGMA user_version=4')

    def _seed_labels(self, db):
        if not self.labels:
            return
        existing = db.execute('SELECT COUNT(*) FROM labels').fetchone()[0]
        if existing:
            return
        now = datetime.now(timezone.utc).isoformat()
        for label in self.labels:
            db.execute('INSERT OR IGNORE INTO labels VALUES(?,?,?)', (label['id'], label['name'], now))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, manifest, run_date):
        canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        now = datetime.now(timezone.utc).isoformat()
        payload = {'id': manifest['run_id'], **manifest, 'run_date': run_date, 'received_at': now, 'upload_state': 'receiving', 'status': 'UNKNOWN', 'issue_count': 0, 'results': [], 'issues': [], 'summary': {'total': 0, 'passed': 0, 'failed': 0, 'errored': 0, 'skipped': 0, 'unknown': 0, 'case_elapsed_ms': 0}}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self.case_sensitive_ids:
                collision = db.execute('SELECT id FROM runs WHERE id = ? COLLATE NOCASE AND id != ? LIMIT 1', (manifest['run_id'], manifest['run_id'])).fetchone()
                if collision:
                    raise DomainError(409, 'Windows 上运行编号不能仅以大小写区分，请使用其他编号')
            row = db.execute('SELECT manifest,payload FROM runs WHERE id=?', (manifest['run_id'],)).fetchone()
            if row:
                if row['manifest'] != canonical:
                    raise DomainError(409, '运行编号已存在，但内容不同；请保留原编号用于重试，新运行使用新编号')
                return json.loads(row['payload']), False
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (manifest['run_id'], canonical, manifest['mode']['id'], manifest['mode']['name'], manifest['started_at'], datetime.fromisoformat(manifest['started_at']).timestamp(), run_date, now, 'receiving', 'UNKNOWN', 0, json.dumps(payload, ensure_ascii=False), 0))
        return payload, True

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise DomainError(404, '未找到本次运行')
        return json.loads(row['payload'])

    def complete(self, run_id, normalized):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload,upload_state FROM runs WHERE id=?', (run_id,)).fetchone()
            if row['upload_state'] == 'complete':
                return json.loads(row['payload'])
            run = json.loads(row['payload'])
            run.update(normalized, upload_state='complete')
            db.execute('UPDATE runs SET upload_state=?,status=?,issue_count=?,attention_count=?,payload=? WHERE id=?', ('complete', run['status'], len(run['issues']), run.get('attention_count', 0), json.dumps(run, ensure_ascii=False), run_id))
            db.executemany('INSERT INTO issues VALUES(?,?,?)', [(run_id, i['category'], i['category_name']) for i in run['issues']])
            return run

    @staticmethod
    def _filters(mode='', date_from='', date_to='', status='', category='', issues_only=False, unannotated=False, label='', q=''):
        terms, args = [], []
        for value, clause in ((mode, 'mode_id=?'), (date_from, 'run_date>=?'), (date_to, 'run_date<=?'), (status, 'status=?')):
            if value:
                terms.append(clause)
                args.append(value)
        if issues_only:
            terms.append('issue_count>0')
        if category:
            terms.append('EXISTS(SELECT 1 FROM issues WHERE run_id=runs.id AND category=?)')
            args.append(category)
        if unannotated:
            terms.append('attention_count > (SELECT COUNT(*) FROM annotations a WHERE a.run_id=runs.id)')
        if label:
            terms.append('EXISTS(SELECT 1 FROM annotation_labels al WHERE al.run_id=runs.id AND al.label_id=?)')
            args.append(label)
        if q:
            terms.append('(instr(lower(id),lower(?))>0 OR instr(lower(mode_name),lower(?))>0)')
            args += [q, q]
        return (' WHERE ' + ' AND '.join(terms) if terms else ''), args

    def list(self, page=1, page_size=20, **filters):
        where, args = self._filters(**filters)
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM runs' + where, args).fetchone()[0]
            stats = dict(db.execute('SELECT COUNT(*) AS runs, COALESCE(SUM(upload_state="complete"),0) AS complete, COALESCE(SUM(issue_count>0),0) AS with_issues, COALESCE(SUM(issue_count),0) AS issues, COALESCE(SUM(status="PASS" AND upload_state="complete"),0) AS passed, COALESCE(SUM(attention_count > (SELECT COUNT(*) FROM annotations a WHERE a.run_id=runs.id)),0) AS needs_annotation FROM runs' + where, args).fetchone())
            rows = db.execute('SELECT payload, attention_count, (SELECT COUNT(*) FROM annotations a WHERE a.run_id=runs.id) AS annotated FROM runs' + where + ' ORDER BY started_epoch DESC,id DESC LIMIT ? OFFSET ?', [*args, page_size, (page-1)*page_size]).fetchall()
        items = []
        for row in rows:
            run = json.loads(row['payload'])
            run.pop('results', None)
            run.pop('recovery_events', None)
            run['attention_count'] = row['attention_count']
            run['annotated_count'] = row['annotated']
            run['categories'] = list({i['category']: {'id': i['category'], 'name': i['category_name']} for i in run.pop('issues', [])}.values())
            items.append(run)
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size, 'stats': stats}

    def options(self):
        with self.connect() as db:
            modes = db.execute('SELECT mode_id AS id, (SELECT r2.mode_name FROM runs r2 WHERE r2.mode_id=r.mode_id ORDER BY received_at DESC LIMIT 1) AS name, COUNT(*) AS count FROM runs r GROUP BY mode_id ORDER BY name').fetchall()
            categories = db.execute('SELECT category AS id,category_name AS name,COUNT(DISTINCT run_id) AS count FROM issues GROUP BY category,category_name ORDER BY name').fetchall()
            labels = db.execute('SELECT id, name, created_at, (SELECT COUNT(*) FROM annotation_labels al WHERE al.label_id=labels.id) AS count FROM labels ORDER BY name').fetchall()
        return {'modes': [dict(x) for x in modes], 'categories': [dict(x) for x in categories], 'labels': [dict(x) for x in labels]}

    def list_labels(self):
        with self.connect() as db:
            rows = db.execute('SELECT id, name, created_at, (SELECT COUNT(*) FROM annotation_labels al WHERE al.label_id=labels.id) AS count FROM labels ORDER BY name').fetchall()
        return [dict(x) for x in rows]

    def add_label(self, label_id, name, created_at):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM labels WHERE name=? COLLATE NOCASE', (name,)).fetchone():
                raise DomainError(409, '标签名称已存在')
            db.execute('INSERT INTO labels VALUES(?,?,?)', (label_id, name, created_at))
        return {'id': label_id, 'name': name, 'created_at': created_at, 'count': 0}

    def rename_label(self, label_id, name):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id, created_at FROM labels WHERE id=?', (label_id,)).fetchone()
            if not row:
                raise DomainError(404, '未找到该标签')
            if db.execute('SELECT id FROM labels WHERE name=? COLLATE NOCASE AND id!=?', (name, label_id)).fetchone():
                raise DomainError(409, '标签名称已存在')
            db.execute('UPDATE labels SET name=? WHERE id=?', (name, label_id))
        return {'id': label_id, 'name': name, 'created_at': row['created_at'], 'count': 0}

    def delete_label(self, label_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM labels WHERE id=?', (label_id,)).fetchone():
                raise DomainError(404, '未找到该标签')
            db.execute('DELETE FROM labels WHERE id=?', (label_id,))

    def resolve_labels(self, label_ids):
        ids = list(dict.fromkeys(label_ids))
        with self.connect() as db:
            placeholders = ','.join('?' * len(ids))
            rows = db.execute(f'SELECT id, name FROM labels WHERE id IN ({placeholders})', ids).fetchall()
        found = {r['id']: r['name'] for r in rows}
        missing = [i for i in ids if i not in found]
        if missing:
            raise DomainError(422, '所选标签不存在或已删除：' + '、'.join(missing))
        return [{'id': i, 'name': found[i]} for i in ids]

    def list_annotations(self, run_id):
        with self.connect() as db:
            rows = db.execute('SELECT result_index, note, author, created_at, updated_at FROM annotations WHERE run_id=? ORDER BY result_index', (run_id,)).fetchall()
            label_rows = db.execute('SELECT result_index, label_id, label_name FROM annotation_labels WHERE run_id=? ORDER BY result_index', (run_id,)).fetchall()
        by_index = {}
        for lr in label_rows:
            by_index.setdefault(lr['result_index'], []).append({'id': lr['label_id'], 'name': lr['label_name']})
        return [{'result_index': row['result_index'], 'labels': by_index.get(row['result_index'], []), 'note': row['note'], 'author': row['author'], 'created_at': row['created_at'], 'updated_at': row['updated_at']} for row in rows]

    def get_annotation(self, run_id, result_index):
        with self.connect() as db:
            row = db.execute('SELECT result_index, note, author, created_at, updated_at FROM annotations WHERE run_id=? AND result_index=?', (run_id, result_index)).fetchone()
            if not row:
                return None
            label_rows = db.execute('SELECT label_id, label_name FROM annotation_labels WHERE run_id=? AND result_index=?', (run_id, result_index)).fetchall()
        return {'result_index': row['result_index'], 'labels': [{'id': l['label_id'], 'name': l['label_name']} for l in label_rows], 'note': row['note'], 'author': row['author'], 'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def upsert_annotation(self, run_id, result_index, labels, note, author, now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO annotations(run_id,result_index,note,author,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,result_index) DO UPDATE SET note=excluded.note, author=excluded.author, updated_at=excluded.updated_at', (run_id, result_index, note, author, now, now))
            db.execute('DELETE FROM annotation_labels WHERE run_id=? AND result_index=?', (run_id, result_index))
            db.executemany('INSERT INTO annotation_labels VALUES(?,?,?,?)', [(run_id, result_index, l['id'], l['name']) for l in labels])
        return self.get_annotation(run_id, result_index)

    def delete_annotation(self, run_id, result_index):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cur = db.execute('DELETE FROM annotations WHERE run_id=? AND result_index=?', (run_id, result_index))
            db.execute('DELETE FROM annotation_labels WHERE run_id=? AND result_index=?', (run_id, result_index))
            if cur.rowcount == 0:
                raise DomainError(404, '该记录没有标注')

    def get_run_meta(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT device, firmware, author, updated_at FROM run_meta WHERE run_id=?', (run_id,)).fetchone()
        if not row:
            return None
        return {'device': row['device'], 'firmware': row['firmware'], 'author': row['author'], 'updated_at': row['updated_at']}

    def upsert_run_meta(self, run_id, device, firmware, author, now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO run_meta(run_id,device,firmware,author,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET device=excluded.device, firmware=excluded.firmware, author=excluded.author, updated_at=excluded.updated_at', (run_id, device, firmware, author, now))
        return self.get_run_meta(run_id)

    def get_analysis(self, scope, kind):
        with self.connect() as db:
            row = db.execute('SELECT payload, model, created_at FROM ai_analyses WHERE scope=? AND kind=?', (scope, kind)).fetchone()
        if not row:
            return None
        return {'analysis': json.loads(row['payload']), 'model': row['model'], 'created_at': row['created_at']}

    def upsert_analysis(self, scope, kind, payload, model, created_at):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO ai_analyses(scope,kind,payload,model,created_at) VALUES(?,?,?,?,?) ON CONFLICT(scope,kind) DO UPDATE SET payload=excluded.payload, model=excluded.model, created_at=excluded.created_at', (scope, kind, json.dumps(payload, ensure_ascii=False), model, created_at))
        return self.get_analysis(scope, kind)

    def find_similar_pass_results(self, mode_id, key_fields, exclude_run_id, limit=3, scan_runs=60):
        """Return recent PASS results matching (scenario, module, name).

        Scans at most ``scan_runs`` completed runs of ``mode_id`` (newest first),
        parsing each run's payload to find matching PASS/SKIP records. Bounded so
        a single analysis never parses an unbounded number of payloads.
        """
        scenario, module, name = key_fields['scenario'], key_fields['module'], key_fields['name']
        with self.connect() as db:
            rows = db.execute('SELECT id, payload FROM runs WHERE upload_state=? AND mode_id=? ORDER BY started_epoch DESC LIMIT ?', ('complete', mode_id, scan_runs)).fetchall()
        matches = []
        for row in rows:
            if row['id'] == exclude_run_id or len(matches) >= limit:
                continue
            payload = json.loads(row['payload'])
            for r in payload.get('results', []):
                if r.get('status') in ('PASS', 'SKIP') and r.get('scenario') == scenario and r.get('module') == module and r.get('name') == name:
                    matches.append({'run_id': row['id'], 'started_at': payload.get('started_at', ''), 'timestamp': r.get('timestamp', ''), 'elapsed_ms': r.get('elapsed_ms', 0), 'status': r['status']})
                    if len(matches) >= limit:
                        break
        return matches

    def trend_snapshot(self, mode='', date_from='', date_to='', limit=200):
        """Lightweight projection of completed runs for cross-run trend analysis."""
        where, args = self._filters(mode=mode, date_from=date_from, date_to=date_to)
        clause = (where + ' AND upload_state=?' if where else ' WHERE upload_state=?')
        args = [*args, 'complete']
        with self.connect() as db:
            rows = db.execute('SELECT id, started_at, status, issue_count, payload FROM runs' + clause + ' ORDER BY started_epoch DESC LIMIT ?', [*args, limit]).fetchall()
        snapshot = []
        for row in rows:
            payload = json.loads(row['payload'])
            attention = [{'name': r.get('name', ''), 'module': r.get('module', ''), 'scenario': r.get('scenario', ''), 'status': r.get('status', '')} for r in payload.get('results', []) if r.get('status') not in ('PASS', 'SKIP')]
            snapshot.append({'run_id': row['id'], 'mode_name': payload.get('mode', {}).get('name', ''), 'started_at': row['started_at'], 'status': row['status'], 'issue_count': row['issue_count'], 'summary': payload.get('summary', {}), 'attention': attention})
        return snapshot
