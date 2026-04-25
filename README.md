# Media Tester

Local desktop test tool for:

- async video generation
- Gemini image generation

Run:

```bash
python tools/media_tester/video_test_gui.py
```

Files:

- `video_test_gui.py`: GUI entrypoint
- `run_video_availability_check.py`: video test logic
- `run_gemini_image_check.py`: Gemini image test logic

Notes:

- GUI now auto-saves the latest video/image configuration to `media_tester_config.db`
- Saved settings are reloaded automatically the next time the app starts
