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
.venv\Scripts\uvicorn app:app --host 127.0.0.1 --port 7860
```
Then open `http://127.0.0.1:7860` in your browser.

Files:

- `app.py`: FastAPI Web Server
- `web/`: Web Frontend (HTML/JS/Tailwind)
- `run_video_availability_check.py`: video test logic
- `run_gemini_image_check.py`: image test logic. Gemini models use `generateContent`; `gpt-image-2` uses official `/v1/images/generations` and `/v1/images/edits` endpoints.

Notes:

- Configurations are auto-saved in your browser's local storage.
- For `gpt-image-2`, leave reference images empty for text-to-image. Add image URLs to test image-to-image via `/v1/images/edits`.
