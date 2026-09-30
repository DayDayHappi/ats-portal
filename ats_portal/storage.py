"""Original-byte storage and bounded text views. No database or HTTP dependencies."""
import hashlib
import os
import re
import tempfile
from pathlib import Path

from .models import DomainError

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
DISPLAY_LIMIT = 8192
WINDOWS_DEVICES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in '123456789¹²³'}


class LocalStorage:
    def __init__(self, root, windows_names=None):
        self.windows_names = os.name == 'nt' if windows_names is None else windows_names
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.staging = self.root / '.staging'
        self.staging.mkdir(exist_ok=True, mode=0o700)

    def path(self, run_id, name):
        # Domain validation already checks names; keep the storage boundary safe too.
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', run_id) or name in ('', '.', '..') or '/' in name or '\\' in name or '\x00' in name:
            raise DomainError(422, '无效文件路径')
        if self.windows_names:
            for component in (run_id, name):
                # Reject '~' too: an NTFS 8.3 alias can otherwise address a
                # different artifact despite distinct manifest filenames.
                if component.endswith((' ', '.')) or component.split('.')[0].rstrip(' ').upper() in WINDOWS_DEVICES or any(c in '<>:"|?*~' or ord(c) < 32 for c in component):
                    raise DomainError(422, 'Windows 归档不接受此运行编号或文件名：' + component)
        return self.root / run_id / name

    def validate_manifest(self, run_id, artifacts):
        names = [a['name'] for a in artifacts]
        for name in names:
            self.path(run_id, name)
        if self.windows_names and len({name.casefold() for name in names}) != len(names):
            raise DomainError(422, 'Windows 文件名不区分大小写，清单中的文件名存在冲突')

    def available(self, run_id, artifact):
        path = self.path(run_id, artifact['name'])
        return path.is_file() and path.stat().st_size == artifact['size']

    async def receive(self, run_id, artifact, chunks):
        target = self.path(run_id, artifact['name'])
        target.parent.mkdir(exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix='upload-', dir=self.staging)
        size = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as output:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > artifact['size']:
                        raise DomainError(422, '文件大小超出登记值')
                    output.write(chunk)
                    digest.update(chunk)
                if size != artifact['size'] or digest.hexdigest() != artifact['sha256']:
                    raise DomainError(422, '文件大小或 SHA256 不匹配，请重试原始文件')
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp, target)
            return size
        finally:
            Path(tmp).unlink(missing_ok=True)

    def read_small(self, run_id, artifact, max_bytes):
        if artifact['size'] > max_bytes:
            raise DomainError(413, f"{artifact['name']} 超出结构化解析大小限制")
        return self.path(run_id, artifact['name']).read_text(encoding='utf-8-sig', errors='replace')

    @staticmethod
    def _lines(path, query=''):
        """Consume even giant lines in pieces, retaining only a bounded display prefix."""
        needle = query.casefold()
        with path.open('r', encoding='utf-8', errors='replace', newline=None) as stream:
            number = 0
            while True:
                piece = stream.readline(65536)
                if not piece:
                    return
                number += 1
                preview, length, tail, matched = '', 0, '', False
                while True:
                    length += len(piece)
                    if len(preview) < DISPLAY_LIMIT:
                        preview += piece[:DISPLAY_LIMIT-len(preview)]
                    if needle:
                        folded = tail + piece.casefold()
                        matched = matched or needle in folded
                        tail = folded[-max(1, len(needle)-1):]
                    if piece.endswith('\n'):
                        break
                    piece = stream.readline(65536)
                    if not piece:
                        break
                yield {'number': number, 'text': ANSI.sub('', preview.rstrip('\r\n')), 'truncated': length > DISPLAY_LIMIT}, matched, length

    def lines(self, path, start, limit):
        lines, more = [], False
        for line, _, _ in self._lines(path):
            if line['number'] < start:
                continue
            if len(lines) == limit:
                more = True
                break
            lines.append(line)
        return {'lines': lines, 'has_more': more, 'next_start': lines[-1]['number'] + 1 if lines else start}

    def search(self, path, query, start, limit):
        matches, scanned, last, more = [], 0, start-1, False
        for line, hit, length in self._lines(path, query):
            if line['number'] < start:
                continue
            if len(matches) >= limit or scanned >= 16 * 1024 * 1024:
                more = True
                break
            scanned += length
            last = line['number']
            if hit:
                matches.append(line)
        return {'matches': matches, 'has_more': more, 'next_start': last + 1, 'scanned_chars': scanned}

    TIMESTAMP = re.compile(r'^\[(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?\]')
    DAY_MS = 86_400_000
    WRAP_THRESHOLD_MS = 6 * 60 * 60 * 1000  # a backward jump this large is a midnight wrap, not jitter

    @classmethod
    def _wall_ms(cls, text):
        """Wall-clock milliseconds from a leading [HH:MM:SS(.mmm)] stamp, or None."""
        match = cls.TIMESTAMP.match(text)
        if not match:
            return None
        hour, minute, second = map(int, match.groups()[:3])
        fraction = (match.group(4) or '').ljust(3, '0')[:3]
        return ((hour * 60 + minute) * 60 + second) * 1000 + int(fraction)

    def locate(self, path, at, marker='TX>', day_offset=0):
        """Line number of the first ``marker`` line at/after wall-clock ``at``.

        ``at`` is ``HH:MM:SS`` or ``HH:MM:SS.mmm``. Log stamps carry no date, so
        ``day_offset`` is the number of midnights the target sits past the log's
        first line; a backward time jump in the log is counted as one wrap. If no
        marker appears at or after ``at``, fall back to the nearest marker before
        it. Returns ``None`` when the log has no marker line at all.
        """
        bracketed = '[' + at + ']'
        if not self.TIMESTAMP.match(bracketed):
            raise DomainError(422, 'locate 需要 HH:MM:SS 或 HH:MM:SS.mmm 的日志时刻')
        target = self._wall_ms(bracketed) + day_offset * self.DAY_MS
        last_marker = None
        wraps = 0
        prev = None
        for line, _, _ in self._lines(path):
            now = self._wall_ms(line['text'])
            if now is None:
                continue
            if prev is not None and now < prev - self.WRAP_THRESHOLD_MS:
                wraps += 1
            prev = now
            if marker in line['text']:
                absolute = now + wraps * self.DAY_MS
                if absolute >= target:
                    return line['number']
                last_marker = line['number']
        return last_marker
