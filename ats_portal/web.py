"""HTTP adapter; domain and storage logic live outside the routes."""
import hashlib
import hmac
import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import ai as ai_module
from .classifier import Classifier
from .config import Settings
from .models import AnalysisRunPost, AnnotationPut, DomainError, Label, Manifest, RunMetaPut, TrendsPost
from .repository import Repository, load_label_seed
from .service import IngestionService
from .storage import LocalStorage


class SessionInput(BaseModel):
    token: str = Field(max_length=2000)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    repository = Repository(settings.data_dir / 'portal.sqlite3', labels=load_label_seed(settings.labels_path))
    storage = LocalStorage(settings.data_dir / 'artifacts')
    ai = ai_module.AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model, settings.ai_timeout_seconds) if settings.ai_enabled else None
    service = IngestionService(settings, repository, storage, Classifier(settings.rules_path), ai=ai)
    app = FastAPI(title='ATS Portal', version='1.0.0', docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.state.service = service
    session_secret = hmac.new(settings.upload_token.encode(), b'ats-portal-read-session-v1', hashlib.sha256).hexdigest()

    def authorized(request):
        expected = 'Bearer ' + settings.upload_token
        return bool(settings.upload_token) and hmac.compare_digest(request.headers.get('authorization', ''), expected)

    def upload_auth(request: Request):
        if not authorized(request):
            raise DomainError(401, '上传需要有效的专用凭据')

    def read_auth(request: Request):
        if settings.require_read_auth and not authorized(request) and not hmac.compare_digest(request.cookies.get('ats_session', ''), session_secret):
            raise DomainError(401, '请填写访问凭据后查看本机测试数据')

    @app.middleware('http')
    async def boundaries(request: Request, call_next):
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            origin = request.headers.get('origin')
            if origin and (urlparse(origin).scheme != request.url.scheme or urlparse(origin).netloc != request.headers.get('host')):
                return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({'detail': exc.message}, status_code=exc.status)

    @app.exception_handler(OSError)
    async def disk_error(request, exc):
        import logging
        logging.getLogger('ats_portal').exception('Local storage error', exc_info=exc)
        return JSONResponse({'detail': '本机存储暂不可用，请检查空间和目录权限后重试'}, status_code=507)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '1.0.0'}

    @app.post('/api/v1/session')
    def session(data: SessionInput):
        if not settings.upload_token or not hmac.compare_digest(data.token, settings.upload_token):
            raise DomainError(401, '访问凭据不正确')
        response = JSONResponse({'ok': True})
        response.set_cookie('ats_session', session_secret, httponly=True, samesite='strict', max_age=8*3600)
        return response

    @app.get('/api/v1/options', dependencies=[Depends(read_auth)])
    def options():
        return {**repository.options(), 'timezone': settings.timezone, 'rules_version': service.classifier.version, 'ai_enabled': settings.ai_enabled, 'ai_model': settings.ai_model if settings.ai_enabled else ''}

    @app.post('/api/v1/runs', dependencies=[Depends(upload_auth)])
    async def register(request: Request):
        # Bound JSON bytes before parsing; upload bodies use their own streaming limit.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 256 * 1024:
                raise DomainError(413, '运行清单过大')
        from pydantic import ValidationError
        try:
            manifest = Manifest.model_validate_json(body)
        except ValidationError as exc:
            raise DomainError(422, str(exc)) from exc
        run, created = service.register(manifest)
        return JSONResponse(receipt(run), status_code=201 if created else 200)

    def receipt(run):
        return {key: run.get(key) for key in ('run_id', 'upload_state', 'status', 'summary')}

    @app.put('/api/v1/runs/{run_id}/artifacts/{name}', dependencies=[Depends(upload_auth)])
    async def put_artifact(run_id: str, name: str, request: Request):
        artifact = service.artifact(run_id, name)
        size = await storage.receive(run_id, artifact, request.stream())
        return {'name': name, 'size': size, 'verified': True}

    @app.post('/api/v1/runs/{run_id}/finalize', dependencies=[Depends(upload_auth)])
    def finalize(run_id: str):
        return receipt(service.finalize(run_id))

    @app.get('/api/v1/runs', dependencies=[Depends(read_auth)])
    def list_runs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), mode: str = '', date_from: date | None = None, date_to: date | None = None, status: str = '', category: str = '', issues_only: bool = False, unannotated: bool = False, label: str = '', q: str = Query('', max_length=200)):
        if date_from and date_to and date_from > date_to:
            raise DomainError(422, '开始日期不能晚于结束日期')
        return repository.list(page=page, page_size=page_size, mode=mode, date_from=str(date_from or ''), date_to=str(date_to or ''), status=status, category=category, issues_only=issues_only, unannotated=unannotated, label=label, q=q)

    @app.get('/api/v1/runs/{run_id}', dependencies=[Depends(read_auth)])
    def get_run(run_id: str):
        return service.detail(run_id)

    @app.get('/api/v1/runs/{run_id}/artifacts/{name}/download', dependencies=[Depends(read_auth)])
    def download(run_id: str, name: str):
        service.artifact(run_id, name, require_file=True)
        return FileResponse(storage.path(run_id, name), media_type='application/octet-stream', filename=name)

    @app.get('/api/v1/runs/{run_id}/artifacts/{name}/lines', dependencies=[Depends(read_auth)])
    def lines(run_id: str, name: str, start: int = Query(1, ge=1), limit: int = Query(150, ge=1, le=300)):
        service.artifact(run_id, name, require_file=True)
        return storage.lines(storage.path(run_id, name), start, limit)

    @app.get('/api/v1/runs/{run_id}/artifacts/{name}/search', dependencies=[Depends(read_auth)])
    def search(run_id: str, name: str, q: str = Query(..., min_length=1, max_length=200), start: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=100)):
        service.artifact(run_id, name, require_file=True)
        return storage.search(storage.path(run_id, name), q, start, limit)

    @app.get('/api/v1/runs/{run_id}/artifacts/{name}/locate', dependencies=[Depends(read_auth)])
    def locate(run_id: str, name: str, at: str = Query(..., min_length=8, max_length=12), day: int = Query(0, ge=0, le=10)):
        service.artifact(run_id, name, require_file=True)
        number = storage.locate(storage.path(run_id, name), at, day_offset=day)
        if number is None:
            raise DomainError(404, '未能在日志中定位到该时刻的发送命令，请手动搜索')
        return {'number': number}

    @app.get('/api/v1/labels', dependencies=[Depends(read_auth)])
    def list_labels():
        return repository.list_labels()

    @app.post('/api/v1/labels', dependencies=[Depends(read_auth)], status_code=201)
    def create_label(data: Label):
        return repository.add_label(secrets.token_hex(6), data.name, datetime.now(timezone.utc).isoformat())

    @app.patch('/api/v1/labels/{label_id}', dependencies=[Depends(read_auth)])
    def rename_label(label_id: str, data: Label):
        return repository.rename_label(label_id, data.name)

    @app.delete('/api/v1/labels/{label_id}', dependencies=[Depends(read_auth)])
    def delete_label(label_id: str):
        repository.delete_label(label_id)
        return Response(status_code=204)

    @app.get('/api/v1/runs/{run_id}/annotations', dependencies=[Depends(read_auth)])
    def list_annotations(run_id: str):
        return repository.list_annotations(run_id)

    @app.get('/api/v1/runs/{run_id}/meta', dependencies=[Depends(read_auth)])
    def get_run_meta(run_id: str):
        service.detail(run_id)
        return repository.get_run_meta(run_id)

    @app.put('/api/v1/runs/{run_id}/meta', dependencies=[Depends(read_auth)])
    def put_run_meta(run_id: str, data: RunMetaPut):
        return service.upsert_run_meta(run_id, data)

    @app.put('/api/v1/runs/{run_id}/results/{result_index}/annotation', dependencies=[Depends(read_auth)])
    def put_annotation(run_id: str, result_index: int, data: AnnotationPut):
        return service.upsert_annotation(run_id, result_index, data)

    @app.delete('/api/v1/runs/{run_id}/results/{result_index}/annotation', dependencies=[Depends(read_auth)])
    def remove_annotation(run_id: str, result_index: int):
        service.delete_annotation(run_id, result_index)
        return Response(status_code=204)

    @app.post('/api/v1/runs/{run_id}/analysis', dependencies=[Depends(read_auth)])
    def analyze_run(run_id: str, data: AnalysisRunPost):
        return service.analyze_run(run_id, data.refresh)

    @app.post('/api/v1/analysis/trends', dependencies=[Depends(read_auth)])
    def analyze_trends(data: TrendsPost):
        return service.analyze_trends(data.mode, data.date_from, data.date_to, data.refresh)

    static = Path(__file__).parent / 'static'
    if static.is_dir():
        app.mount('/', StaticFiles(directory=static, html=True), name='ui')
    return app
