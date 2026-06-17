import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

APP_DIR = Path(__file__).resolve().parent
INDEX_PATH = APP_DIR / "index.html"
NOT_FOUND_PATH = APP_DIR / "404.html"
STATIC_DIR = APP_DIR / "static"

HTML_CACHE_CONTROL = "no-cache"
UNVERSIONED_ASSET_CACHE_CONTROL = "public, max-age=0, must-revalidate"
VERSIONED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"

FINGERPRINTED_FILENAME_RE = re.compile(
    r"(?:^|[._-])[0-9a-f]{8,}(?:[._-]|$)",
    re.IGNORECASE,
)


def cache_control_for_static_path(path: str) -> str:
    """Use long-lived caching only for fingerprinted asset filenames."""
    filename = Path(path).name
    if FINGERPRINTED_FILENAME_RE.search(filename):
        return VERSIONED_ASSET_CACHE_CONTROL
    return UNVERSIONED_ASSET_CACHE_CONTROL


class CacheControlStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault(
                "Cache-Control",
                cache_control_for_static_path(path),
            )
        return response


app = FastAPI()

app.mount(
    "/static",
    CacheControlStaticFiles(directory=STATIC_DIR),
    name="static",
)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(
        INDEX_PATH,
        media_type="text/html",
        headers={"Cache-Control": HTML_CACHE_CONTROL},
    )


@app.exception_handler(StarletteHTTPException)
async def not_found_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """Serve the styled 404 page for unmatched routes."""
    if exc.status_code == 404:
        return FileResponse(
            NOT_FOUND_PATH,
            status_code=404,
            media_type="text/html",
            headers={"Cache-Control": HTML_CACHE_CONTROL},
        )
    return Response(exc.detail, status_code=exc.status_code)
