# portrait-studio API

FastAPI backend of portrait-studio. See `../README.md` for the whole application and `../docs/` for setup.

```bash
uv sync                      # create .venv (Python 3.12)
cp ../.env.example ../.env   # then set API_KEY
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run pytest -q             # tests run against a mock ComfyUI and a mock face engine (no GPU needed)
uv run ruff check . && uv run ruff format --check . && uv run mypy app
```
