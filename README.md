# Media Tester

Local web-based test tool for:

- async video generation
- Gemini image generation
- OpenAI Images API compatible `gpt-image-2` generation/editing

Video models currently exposed in the UI include `kling-video-3.0`,
`kling-video-o3-omni`, `sora2`, `sora-v3-pro`, `sora-v3-fast`, `veo31-fast`,
and `gemini-omni-flash`.

`gemini-omni-flash` supports `16:9` / `9:16`, durations `4` / `6` / `8` /
`10` seconds, up to 5 reference images, and 1 reference video. The UI does not
show or send a resolution parameter for this model; the service handles its
output resolution internally.

Run:

Double click `start.bat` on Windows.
Or manually via terminal:
```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app:app --host 0.0.0.0 --port 5800
```
Then open `http://127.0.0.1:5800` in your browser.

Default login:

- Username: `admin`
- Password: `admin`

Change the login username/password from the system configuration button in the top-right corner after signing in.

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
- `tests/run_video_availability_check.py`: video test logic
- `tests/run_gemini_image_check.py`: image test logic. Gemini models use `generateContent`; `gpt-image-2` uses official `/v1/images/generations` and `/v1/images/edits` endpoints.

Notes:

- Configurations are auto-saved to the server-side SQLite database at `data/config.db`.
- Change the default login password before exposing the service on a public server.
- For `gpt-image-2`, leave reference images empty for text-to-image. Add image URLs to test image-to-image via `/v1/images/edits`.
