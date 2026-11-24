# website

The shortcake.patrick.wtf landing page, served by a small FastAPI app.

## Quick Start

### Start the development server

```bash
uv run fastapi dev
```

Visit http://localhost:8000

### Deploy

The site runs on Laravel Cloud (app `shortcake-website`, root directory
`website/`). Every push to `main` deploys it.

## Project Structure

- `main.py` - The FastAPI application
- `index.html`, `404.html`, `static/` - The site's pages and assets
- `pyproject.toml` - Project dependencies

## Learn More

- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [Laravel Cloud](https://cloud.laravel.com/docs)
