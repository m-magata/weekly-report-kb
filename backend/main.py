from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from backend.routers.upload import router as upload_router
from backend.routers.reports import router as reports_router
from backend.routers.search import router as search_router

app = FastAPI(title="週報ナレッジDB", version="0.1.0")
app.include_router(upload_router)
app.include_router(reports_router)
app.include_router(search_router)

# フロントエンド静的ファイル配信
# __file__ から絶対パスを解決（起動ディレクトリに依存しない）
_root     = Path(__file__).resolve().parent.parent
_viewer   = _root / "frontend" / "viewer"
_uploader = _root / "frontend" / "upload"
_uploader.mkdir(parents=True, exist_ok=True)


def _no_cache(resp: FileResponse) -> FileResponse:
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"]  = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.get("/viewer/", include_in_schema=False)
@app.get("/viewer", include_in_schema=False)
def viewer_index():
    return _no_cache(FileResponse(str(_viewer / "index.html"), media_type="text/html"))


@app.get("/upload/", include_in_schema=False)
@app.get("/upload", include_in_schema=False)
def upload_index():
    return _no_cache(FileResponse(str(_uploader / "index.html"), media_type="text/html"))


app.mount("/viewer", StaticFiles(directory=str(_viewer),   html=True), name="viewer")
app.mount("/upload", StaticFiles(directory=str(_uploader), html=True), name="uploader")


@app.get("/health")
def health():
    return {"status": "ok"}
