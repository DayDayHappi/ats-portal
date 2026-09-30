"""SQLite persistence boundary. Parameterized SQL and explicit transactions."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .models import DomainError


class Repository:
    def __init__(self, path, case_sensitive_ids=None):
        self.path = path
        self.case_sensitive_ids = os.name != 'nt' if case_sensitive_ids is None else case_sensitive_ids
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError('数据库来自更新版本，请升级程序，禁止降级覆盖数据')
            if version == 0:
                db.executescript('''
                CREATE TABLE runs (
                    id TEXT PRIMARY KEY, manifest TEXT NOT NULL,
                    mode_id TEXT NOT NULL, mode_name TEXT NOT NULL,
                    started_at TEXT NOT NULL, started_epoch REAL NOT NULL,
                    run_date TEXT NOT NULL, received_at TEXT NOT NULL,
                    upload_state TEXT NOT NULL, status TEXT NOT NULL,
                    issue_count INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                );
                CREATE INDEX runs_date ON runs(run_date, started_epoch DESC);
                CREATE INDEX runs_mode ON runs(mode_id, started_epoch DESC);
                CREATE TABLE issues (run_id TEXT NOT NULL REFERENCES runs(id), category TEXT NOT NULL, category_name TEXT NOT NULL);
                CREATE INDEX issues_category ON issues(category,run_id);
                PRAGMA user_version=1;
                ''')

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
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (manifest['run_id'], canonical, manifest['mode']['id'], manifest['mode']['name'], manifest['started_at'], datetime.fromisoformat(manifest['started_at']).timestamp(), run_date, now, 'receiving', 'UNKNOWN', 0, json.dumps(payload, ensure_ascii=False)))
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
            db.execute('UPDATE runs SET upload_state=?,status=?,issue_count=?,payload=? WHERE id=?', ('complete', run['status'], len(run['issues']), json.dumps(run, ensure_ascii=False), run_id))
            db.executemany('INSERT INTO issues VALUES(?,?,?)', [(run_id, i['category'], i['category_name']) for i in run['issues']])
            return run

    @staticmethod
    def _filters(mode='', date_from='', date_to='', status='', category='', issues_only=False, q=''):
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
        if q:
            terms.append('(instr(lower(id),lower(?))>0 OR instr(lower(mode_name),lower(?))>0)')
            args += [q, q]
        return (' WHERE ' + ' AND '.join(terms) if terms else ''), args

    def list(self, page=1, page_size=20, **filters):
        where, args = self._filters(**filters)
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM runs' + where, args).fetchone()[0]
            stats = dict(db.execute('SELECT COUNT(*) AS runs, COALESCE(SUM(upload_state="complete"),0) AS complete, COALESCE(SUM(issue_count>0),0) AS with_issues, COALESCE(SUM(issue_count),0) AS issues, COALESCE(SUM(status="PASS" AND upload_state="complete"),0) AS passed FROM runs' + where, args).fetchone())
            rows = db.execute('SELECT payload FROM runs' + where + ' ORDER BY started_epoch DESC,id DESC LIMIT ? OFFSET ?', [*args, page_size, (page-1)*page_size]).fetchall()
        items = []
        for row in rows:
            run = json.loads(row['payload'])
            run.pop('results', None)
            run.pop('recovery_events', None)
            run['categories'] = list({i['category']: {'id': i['category'], 'name': i['category_name']} for i in run.pop('issues', [])}.values())
            items.append(run)
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size, 'stats': stats}

    def options(self):
        with self.connect() as db:
            modes = db.execute('SELECT mode_id AS id, (SELECT r2.mode_name FROM runs r2 WHERE r2.mode_id=r.mode_id ORDER BY received_at DESC LIMIT 1) AS name, COUNT(*) AS count FROM runs r GROUP BY mode_id ORDER BY name').fetchall()
            categories = db.execute('SELECT category AS id,category_name AS name,COUNT(DISTINCT run_id) AS count FROM issues GROUP BY category,category_name ORDER BY name').fetchall()
        return {'modes': [dict(x) for x in modes], 'categories': [dict(x) for x in categories]}
