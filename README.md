# Media Tester

Local web-based test tool for:

- async video generation
- Gemini image generation
- OpenAI Images API compatible `gpt-image-2` generation/editing

Run:

Double click `start.bat` on Windows.
Or manually via terminal:
```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app:app --host 0.0.0.0 --port 5800
```
Then open `http://127.0.0.1:5800` in your browser.

Docker:

```bash
docker compose up -d --build
```

Then open `http://server-ip:5800`.

Generated assets are saved under `downloads/`. Runtime settings, including API keys, are saved in `data/config.db`.
The Compose file mounts `./downloads` and `./data` so generated assets and settings persist after container restarts.

Files:

- `app.py`: FastAPI Web Server
- `web/`: Web Frontend (HTML/JS/Tailwind)
- `run_video_availability_check.py`: video test logic
- `run_gemini_image_check.py`: image test logic. Gemini models use `generateContent`; `gpt-image-2` uses official `/v1/images/generations` and `/v1/images/edits` endpoints.

Notes:

- Configurations are auto-saved to the server-side SQLite database at `data/config.db`.
- For `gpt-image-2`, leave reference images empty for text-to-image. Add image URLs to test image-to-image via `/v1/images/edits`.
