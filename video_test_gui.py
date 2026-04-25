import io
import json
import queue
import sqlite3
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk

import run_gemini_image_check as image_runner
import run_video_availability_check as video_runner


class QueueWriter:
    def __init__(self, callback) -> None:
        self.callback = callback

    def write(self, text: str) -> int:
        if text:
            self.callback(text)
        return len(text)

    def flush(self) -> None:
        return None


class ConfigStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS configs (
                    config_name TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save(self, config_name: str, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO configs (config_name, config_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(config_name) DO UPDATE SET
                    config_json = excluded.config_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (config_name, payload),
            )

    def load(self, config_name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT config_json FROM configs WHERE config_name = ?",
                (config_name,),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        if not isinstance(data, dict):
            raise RuntimeError("stored config must be a JSON object")
        return data


class MediaTestGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Media Test Tool")
        self.root.geometry("1280x920")
        self.config_store = ConfigStore(
            Path(__file__).resolve().with_name("media_tester_config.db")
        )

        self.log_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.preview_image: ImageTk.PhotoImage | None = None

        self.video_status_var = tk.StringVar(value="Ready")
        self.image_status_var = tk.StringVar(value="Ready")

        self._init_video_vars()
        self._init_image_vars()
        self._build_ui()
        self._load_persisted_configs()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._flush_log_queue)

    def _init_video_vars(self) -> None:
        self.video_base_url_var = tk.StringVar(value=video_runner.DEFAULT_BASE_URL)
        self.video_api_key_var = tk.StringVar()
        self.video_create_path_var = tk.StringVar(
            value=video_runner.DEFAULT_CREATE_PATH
        )
        self.video_status_path_var = tk.StringVar(
            value=video_runner.DEFAULT_STATUS_PATH
        )
        self.video_model_var = tk.StringVar(value="sora2")
        self.video_prompt_var = tk.StringVar(value=video_runner.DEFAULT_PROMPT)
        self.video_aspect_ratio_var = tk.StringVar(
            value=video_runner.DEFAULT_ASPECT_RATIO
        )
        self.video_size_var = tk.StringVar()
        self.video_duration_var = tk.StringVar(value=str(video_runner.DEFAULT_DURATION))
        self.video_seconds_var = tk.StringVar()
        self.video_resolution_var = tk.StringVar()
        self.video_reference_field_var = tk.StringVar(value="input_reference")
        self.video_reference_format_var = tk.StringVar(value="array")
        self.video_timeout_var = tk.StringVar(
            value=str(video_runner.DEFAULT_TIMEOUT_SECONDS)
        )
        self.video_poll_interval_var = tk.StringVar(
            value=str(video_runner.DEFAULT_POLL_INTERVAL)
        )
        self.video_request_timeout_var = tk.StringVar(
            value=str(video_runner.DEFAULT_REQUEST_TIMEOUT)
        )
        self.video_out_dir_var = tk.StringVar(value=str(video_runner.DEFAULT_OUT_DIR))
        self.video_output_name_var = tk.StringVar(
            value=video_runner.DEFAULT_OUTPUT_NAME
        )
        self.video_download_check_var = tk.BooleanVar(value=False)
        self.video_skip_head_check_var = tk.BooleanVar(value=False)
        self.video_print_payload_var = tk.BooleanVar(value=False)

    def _init_image_vars(self) -> None:
        self.image_base_url_var = tk.StringVar(value=image_runner.DEFAULT_BASE_URL)
        self.image_api_key_var = tk.StringVar()
        self.image_model_var = tk.StringVar(value=image_runner.DEFAULT_MODEL)
        self.image_prompt_var = tk.StringVar(value=image_runner.DEFAULT_PROMPT)
        self.image_aspect_ratio_var = tk.StringVar(
            value=image_runner.DEFAULT_ASPECT_RATIO
        )
        self.image_size_var = tk.StringVar(value=image_runner.DEFAULT_IMAGE_SIZE)
        self.image_request_timeout_var = tk.StringVar(
            value=str(image_runner.DEFAULT_REQUEST_TIMEOUT)
        )
        self.image_out_dir_var = tk.StringVar(value=str(image_runner.DEFAULT_OUT_DIR))
        self.image_output_name_var = tk.StringVar(
            value=image_runner.DEFAULT_OUTPUT_NAME
        )
        self.image_save_var = tk.BooleanVar(value=True)
        self.image_print_payload_var = tk.BooleanVar(value=False)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        video_tab = ttk.Frame(notebook, padding=10)
        image_tab = ttk.Frame(notebook, padding=10)
        notebook.add(video_tab, text="Video Test")
        notebook.add(image_tab, text="Gemini Image Test")

        self._build_video_tab(video_tab)
        self._build_image_tab(image_tab)

    def _build_video_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        left = ttk.LabelFrame(top, text="Connection", padding=10)
        right = ttk.LabelFrame(top, text="Request", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left.columnconfigure(1, weight=1)
        right.columnconfigure(1, weight=1)

        self._add_entry(left, 0, "Base URL", self.video_base_url_var)
        self._add_entry(left, 1, "API Key", self.video_api_key_var, show="*")
        self._add_entry(left, 2, "Create Path", self.video_create_path_var)
        self._add_entry(left, 3, "Status Path", self.video_status_path_var)
        self._add_combo(
            left,
            4,
            "Model",
            self.video_model_var,
            ["sora2", "veo31", "veo31-fast", "veo31-ref"],
        )
        self._add_combo(
            left, 5, "Aspect Ratio", self.video_aspect_ratio_var, ["16:9", "9:16", ""]
        )
        self._add_combo(
            left, 6, "Size", self.video_size_var, ["", "1280x720", "720x1280"]
        )
        self._add_combo(
            left, 7, "Resolution", self.video_resolution_var, ["", "1080p", "720p"]
        )
        self._add_entry(left, 8, "Duration", self.video_duration_var)
        self._add_entry(left, 9, "Seconds", self.video_seconds_var)

        ttk.Label(right, text="Prompt").grid(row=0, column=0, sticky="nw", pady=4)
        self.video_prompt_text = ScrolledText(right, height=7, wrap="word")
        self.video_prompt_text.grid(row=0, column=1, sticky="ew", pady=4)
        self.video_prompt_text.insert("1.0", self.video_prompt_var.get())
        self._add_entry(right, 1, "Timeout", self.video_timeout_var)
        self._add_entry(right, 2, "Poll Interval", self.video_poll_interval_var)
        self._add_entry(right, 3, "Request Timeout", self.video_request_timeout_var)
        self._add_entry(right, 4, "Output Dir", self.video_out_dir_var)
        ttk.Button(right, text="Browse", command=self._browse_video_out_dir).grid(
            row=4, column=2, sticky="w", padx=(6, 0)
        )
        self._add_entry(right, 5, "Output Name", self.video_output_name_var)

        checks = ttk.Frame(right)
        checks.grid(row=6, column=1, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            checks, text="Download result", variable=self.video_download_check_var
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Checkbutton(
            checks, text="Skip HEAD check", variable=self.video_skip_head_check_var
        ).grid(row=0, column=1, sticky="w", padx=(0, 10))
        ttk.Checkbutton(
            checks, text="Print payload", variable=self.video_print_payload_var
        ).grid(row=0, column=2, sticky="w")

        middle = ttk.Frame(parent)
        middle.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        middle.columnconfigure(0, weight=1)
        middle.columnconfigure(1, weight=1)

        refs = ttk.LabelFrame(middle, text="Reference Images", padding=10)
        advanced = ttk.LabelFrame(middle, text="Advanced JSON", padding=10)
        refs.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        advanced.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        refs.columnconfigure(0, weight=1)
        refs.rowconfigure(3, weight=1)
        advanced.columnconfigure(0, weight=1)
        advanced.rowconfigure(1, weight=1)

        header = ttk.Frame(refs)
        header.grid(row=0, column=0, sticky="ew")
        self._add_combo_inline(
            header,
            0,
            "Field",
            self.video_reference_field_var,
            ["input_reference", "image_urls"],
        )
        self._add_combo_inline(
            header, 2, "Format", self.video_reference_format_var, ["array", "string"]
        )
        ttk.Label(refs, text="Image URLs / data URLs (one per line)").grid(
            row=1, column=0, sticky="w", pady=(8, 4)
        )
        self.video_image_url_text = ScrolledText(refs, height=5, wrap="word")
        self.video_image_url_text.grid(row=2, column=0, sticky="ew")

        file_frame = ttk.Frame(refs)
        file_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        file_frame.columnconfigure(0, weight=1)
        file_frame.rowconfigure(1, weight=1)
        ttk.Label(file_frame, text="Local image files").grid(
            row=0, column=0, sticky="w"
        )
        self.video_file_list = tk.Listbox(file_frame, height=6)
        self.video_file_list.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        file_buttons = ttk.Frame(file_frame)
        file_buttons.grid(row=1, column=1, sticky="ns", padx=(8, 0), pady=(4, 0))
        ttk.Button(file_buttons, text="Add", command=self._add_video_files).grid(
            row=0, column=0, sticky="ew", pady=(0, 6)
        )
        ttk.Button(
            file_buttons, text="Remove", command=self._remove_selected_video_file
        ).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(file_buttons, text="Clear", command=self._clear_video_files).grid(
            row=2, column=0, sticky="ew"
        )

        ttk.Label(
            advanced, text='Extra fields JSON, for example {"enable_audio": true}'
        ).grid(row=0, column=0, sticky="w")
        self.video_extra_text = ScrolledText(advanced, height=12, wrap="word")
        self.video_extra_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

        log_frame = ttk.LabelFrame(parent, text="Logs", padding=10)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.video_log_text = ScrolledText(
            log_frame, height=18, wrap="word", state="disabled"
        )
        self.video_log_text.grid(row=0, column=0, sticky="nsew")

        actions = ttk.Frame(parent)
        actions.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Button(
            actions, text="Start Video Test", command=self.start_video_test
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(actions, textvariable=self.video_status_var).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Button(
            actions, text="Save Video Config", command=self.save_video_config
        ).grid(row=0, column=2, sticky="e", padx=(0, 6))
        ttk.Button(
            actions, text="Load Video Config", command=self.load_video_config
        ).grid(row=0, column=3, sticky="e")

    def _build_image_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        left = ttk.LabelFrame(top, text="Connection", padding=10)
        right = ttk.LabelFrame(top, text="Request", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left.columnconfigure(1, weight=1)
        right.columnconfigure(1, weight=1)

        self._add_entry(left, 0, "Base URL", self.image_base_url_var)
        self._add_entry(left, 1, "API Key", self.image_api_key_var, show="*")
        self._add_combo(
            left, 2, "Model", self.image_model_var, image_runner.MODEL_OPTIONS
        )
        self._add_combo(
            left,
            3,
            "Aspect Ratio",
            self.image_aspect_ratio_var,
            image_runner.ASPECT_RATIO_OPTIONS,
        )
        self._add_combo(
            left, 4, "Image Size", self.image_size_var, image_runner.IMAGE_SIZE_OPTIONS
        )
        self._add_entry(left, 5, "Request Timeout", self.image_request_timeout_var)
        self._add_entry(left, 6, "Output Dir", self.image_out_dir_var)
        ttk.Button(left, text="Browse", command=self._browse_image_out_dir).grid(
            row=6, column=2, sticky="w", padx=(6, 0)
        )
        self._add_entry(left, 7, "Output Name", self.image_output_name_var)

        ttk.Label(right, text="Prompt").grid(row=0, column=0, sticky="nw", pady=4)
        self.image_prompt_text = ScrolledText(right, height=6, wrap="word")
        self.image_prompt_text.grid(row=0, column=1, sticky="ew", pady=4)
        self.image_prompt_text.insert("1.0", self.image_prompt_var.get())

        ttk.Label(right, text="Reference image URLs (one per line)").grid(
            row=1, column=0, sticky="nw", pady=4
        )
        self.image_url_text = ScrolledText(right, height=5, wrap="word")
        self.image_url_text.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(right, text="Local image files").grid(
            row=2, column=0, sticky="nw", pady=4
        )
        image_file_panel = ttk.Frame(right)
        image_file_panel.grid(row=2, column=1, sticky="ew", pady=4)
        image_file_panel.columnconfigure(0, weight=1)
        self.image_file_list = tk.Listbox(image_file_panel, height=5)
        self.image_file_list.grid(row=0, column=0, sticky="ew")
        image_file_buttons = ttk.Frame(image_file_panel)
        image_file_buttons.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        ttk.Button(image_file_buttons, text="Add", command=self._add_image_files).grid(
            row=0, column=0, sticky="ew", pady=(0, 6)
        )
        ttk.Button(
            image_file_buttons, text="Remove", command=self._remove_selected_image_file
        ).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(
            image_file_buttons, text="Clear", command=self._clear_image_files
        ).grid(row=2, column=0, sticky="ew")

        opts = ttk.Frame(right)
        opts.grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            opts, text="Save decoded image", variable=self.image_save_var
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Checkbutton(
            opts, text="Print payload", variable=self.image_print_payload_var
        ).grid(row=0, column=1, sticky="w")

        middle = ttk.Frame(parent)
        middle.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        middle.columnconfigure(0, weight=1)
        middle.columnconfigure(1, weight=1)
        middle.rowconfigure(0, weight=1)

        preview = ttk.LabelFrame(middle, text="Image Preview", padding=10)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.image_preview_label = ttk.Label(
            preview, text="No image yet", anchor="center"
        )
        self.image_preview_label.grid(row=0, column=0, sticky="nsew")

        logs = ttk.LabelFrame(middle, text="Logs", padding=10)
        logs.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        logs.columnconfigure(0, weight=1)
        logs.rowconfigure(0, weight=1)
        self.image_log_text = ScrolledText(
            logs, height=20, wrap="word", state="disabled"
        )
        self.image_log_text.grid(row=0, column=0, sticky="nsew")

        actions = ttk.Frame(parent)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Button(
            actions, text="Start Image Test", command=self.start_image_test
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(actions, textvariable=self.image_status_var).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Button(
            actions, text="Save Image Config", command=self.save_image_config
        ).grid(row=0, column=2, sticky="e", padx=(0, 6))
        ttk.Button(
            actions, text="Load Image Config", command=self.load_image_config
        ).grid(row=0, column=3, sticky="e")

    def _add_entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        show: str | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable, show=show or "").grid(
            row=row, column=1, sticky="ew", pady=4
        )

    def _add_combo(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: list[str],
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(parent, textvariable=variable, values=values).grid(
            row=row, column=1, sticky="ew", pady=4
        )

    def _add_combo_inline(
        self,
        parent: ttk.Frame,
        column: int,
        label: str,
        variable: tk.StringVar,
        values: list[str],
    ) -> None:
        ttk.Label(parent, text=label).grid(row=0, column=column, sticky="w")
        ttk.Combobox(parent, textvariable=variable, values=values, width=16).grid(
            row=0, column=column + 1, sticky="w", padx=(6, 12)
        )

    def _browse_video_out_dir(self) -> None:
        path = filedialog.askdirectory(
            initialdir=self.video_out_dir_var.get() or str(Path.cwd())
        )
        if path:
            self.video_out_dir_var.set(path)

    def _browse_image_out_dir(self) -> None:
        path = filedialog.askdirectory(
            initialdir=self.image_out_dir_var.get() or str(Path.cwd())
        )
        if path:
            self.image_out_dir_var.set(path)

    def _choose_image_files(self) -> tuple[str, ...]:
        return filedialog.askopenfilenames(
            title="Select image files",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
                ("All Files", "*.*"),
            ],
        )

    def _add_video_files(self) -> None:
        for path in self._choose_image_files():
            self.video_file_list.insert(tk.END, path)

    def _remove_selected_video_file(self) -> None:
        for index in reversed(self.video_file_list.curselection()):
            self.video_file_list.delete(index)

    def _clear_video_files(self) -> None:
        self.video_file_list.delete(0, tk.END)

    def _add_image_files(self) -> None:
        for path in self._choose_image_files():
            self.image_file_list.insert(tk.END, path)

    def _remove_selected_image_file(self) -> None:
        for index in reversed(self.image_file_list.curselection()):
            self.image_file_list.delete(index)

    def _clear_image_files(self) -> None:
        self.image_file_list.delete(0, tk.END)

    def _append_log(self, widget: ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.insert(tk.END, text)
        widget.see(tk.END)
        widget.configure(state="disabled")

    def _flush_log_queue(self) -> None:
        while True:
            try:
                kind, payload = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "video_log":
                self._append_log(self.video_log_text, str(payload))
            elif kind == "image_log":
                self._append_log(self.image_log_text, str(payload))
            elif kind == "video_status":
                self.video_status_var.set(str(payload))
            elif kind == "image_status":
                self.image_status_var.set(str(payload))
            elif kind == "image_preview":
                self._show_image_preview(payload)  # type: ignore[arg-type]
            elif kind == "worker_done":
                self.worker = None
        self.root.after(120, self._flush_log_queue)

    def _video_log(self, text: str) -> None:
        self.log_queue.put(("video_log", text))

    def _image_log(self, text: str) -> None:
        self.log_queue.put(("image_log", text))

    def _show_image_preview(self, image_bytes: bytes) -> None:
        image = Image.open(io.BytesIO(image_bytes))
        image.thumbnail((560, 560))
        self.preview_image = ImageTk.PhotoImage(image)
        self.image_preview_label.configure(image=self.preview_image, text="")

    def _collect_listbox(self, widget: tk.Listbox) -> list[str]:
        return [widget.get(index) for index in range(widget.size())]

    def _parse_json_object(self, text: str) -> dict:
        raw = text.strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("JSON value must be an object")
        return data

    def _video_settings(self) -> dict:
        extra_field_obj = self._parse_json_object(
            self.video_extra_text.get("1.0", tk.END)
        )
        extra_fields = [
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in extra_field_obj.items()
        ]
        return {
            "base_url": self.video_base_url_var.get().strip(),
            "api_key": self.video_api_key_var.get().strip(),
            "create_path": self.video_create_path_var.get().strip(),
            "status_path": self.video_status_path_var.get().strip(),
            "model": self.video_model_var.get().strip(),
            "prompt": self.video_prompt_text.get("1.0", tk.END).strip(),
            "aspect_ratio": self.video_aspect_ratio_var.get().strip(),
            "size": self.video_size_var.get().strip() or None,
            "duration": int(self.video_duration_var.get().strip())
            if self.video_duration_var.get().strip()
            else None,
            "seconds": int(self.video_seconds_var.get().strip())
            if self.video_seconds_var.get().strip()
            else None,
            "resolution": self.video_resolution_var.get().strip() or None,
            "image_url": [
                line.strip()
                for line in self.video_image_url_text.get("1.0", tk.END).splitlines()
                if line.strip()
            ],
            "image_file": self._collect_listbox(self.video_file_list),
            "reference_field": self.video_reference_field_var.get().strip(),
            "reference_format": self.video_reference_format_var.get().strip(),
            "extra_field": extra_fields,
            "timeout": int(self.video_timeout_var.get().strip()),
            "poll_interval": float(self.video_poll_interval_var.get().strip()),
            "request_timeout": int(self.video_request_timeout_var.get().strip()),
            "out_dir": self.video_out_dir_var.get().strip(),
            "output_name": self.video_output_name_var.get().strip(),
            "download_check": bool(self.video_download_check_var.get()),
            "skip_head_check": bool(self.video_skip_head_check_var.get()),
            "print_payload": bool(self.video_print_payload_var.get()),
        }

    def _image_settings(self) -> dict:
        return {
            "base_url": self.image_base_url_var.get().strip(),
            "api_key": self.image_api_key_var.get().strip(),
            "model": self.image_model_var.get().strip(),
            "prompt": self.image_prompt_text.get("1.0", tk.END).strip(),
            "aspect_ratio": self.image_aspect_ratio_var.get().strip(),
            "image_size": self.image_size_var.get().strip(),
            "image_url": [
                line.strip()
                for line in self.image_url_text.get("1.0", tk.END).splitlines()
                if line.strip()
            ],
            "image_file": self._collect_listbox(self.image_file_list),
            "request_timeout": int(self.image_request_timeout_var.get().strip()),
            "out_dir": self.image_out_dir_var.get().strip(),
            "output_name": self.image_output_name_var.get().strip(),
            "save_image": bool(self.image_save_var.get()),
            "print_payload": bool(self.image_print_payload_var.get()),
        }

    def _apply_video_settings(self, data: dict) -> None:
        self.video_base_url_var.set(
            str(data.get("base_url") or video_runner.DEFAULT_BASE_URL)
        )
        self.video_api_key_var.set(str(data.get("api_key") or ""))
        self.video_create_path_var.set(
            str(data.get("create_path") or video_runner.DEFAULT_CREATE_PATH)
        )
        self.video_status_path_var.set(
            str(data.get("status_path") or video_runner.DEFAULT_STATUS_PATH)
        )
        self.video_model_var.set(str(data.get("model") or "sora2"))
        self.video_aspect_ratio_var.set(
            str(data.get("aspect_ratio") or video_runner.DEFAULT_ASPECT_RATIO)
        )
        self.video_size_var.set(str(data.get("size") or ""))
        self.video_duration_var.set(
            "" if data.get("duration") is None else str(data.get("duration"))
        )
        self.video_seconds_var.set(
            "" if data.get("seconds") is None else str(data.get("seconds"))
        )
        self.video_resolution_var.set(str(data.get("resolution") or ""))
        self.video_reference_field_var.set(
            str(data.get("reference_field") or "input_reference")
        )
        self.video_reference_format_var.set(
            str(data.get("reference_format") or "array")
        )
        self.video_timeout_var.set(
            str(data.get("timeout") or video_runner.DEFAULT_TIMEOUT_SECONDS)
        )
        self.video_poll_interval_var.set(
            str(data.get("poll_interval") or video_runner.DEFAULT_POLL_INTERVAL)
        )
        self.video_request_timeout_var.set(
            str(data.get("request_timeout") or video_runner.DEFAULT_REQUEST_TIMEOUT)
        )
        self.video_out_dir_var.set(
            str(data.get("out_dir") or video_runner.DEFAULT_OUT_DIR)
        )
        self.video_output_name_var.set(
            str(data.get("output_name") or video_runner.DEFAULT_OUTPUT_NAME)
        )
        self.video_download_check_var.set(bool(data.get("download_check")))
        self.video_skip_head_check_var.set(bool(data.get("skip_head_check")))
        self.video_print_payload_var.set(bool(data.get("print_payload")))
        self.video_prompt_text.delete("1.0", tk.END)
        self.video_prompt_text.insert(
            "1.0", str(data.get("prompt") or video_runner.DEFAULT_PROMPT)
        )
        self.video_image_url_text.delete("1.0", tk.END)
        self.video_image_url_text.insert("1.0", "\n".join(data.get("image_url") or []))
        self.video_file_list.delete(0, tk.END)
        for item in data.get("image_file") or []:
            self.video_file_list.insert(tk.END, str(item))
        extra = data.get("extra_field") or []
        if isinstance(extra, list):
            extra_obj = {}
            for item in extra:
                key, _, raw = str(item).partition("=")
                if key:
                    extra_obj[key] = json.loads(raw)
        else:
            extra_obj = extra if isinstance(extra, dict) else {}
        self.video_extra_text.delete("1.0", tk.END)
        if extra_obj:
            self.video_extra_text.insert(
                "1.0", json.dumps(extra_obj, ensure_ascii=False, indent=2)
            )

    def _apply_image_settings(self, data: dict) -> None:
        self.image_base_url_var.set(
            str(data.get("base_url") or image_runner.DEFAULT_BASE_URL)
        )
        self.image_api_key_var.set(str(data.get("api_key") or ""))
        self.image_model_var.set(str(data.get("model") or image_runner.DEFAULT_MODEL))
        self.image_aspect_ratio_var.set(
            str(data.get("aspect_ratio") or image_runner.DEFAULT_ASPECT_RATIO)
        )
        self.image_size_var.set(
            str(data.get("image_size") or image_runner.DEFAULT_IMAGE_SIZE)
        )
        self.image_request_timeout_var.set(
            str(data.get("request_timeout") or image_runner.DEFAULT_REQUEST_TIMEOUT)
        )
        self.image_out_dir_var.set(
            str(data.get("out_dir") or image_runner.DEFAULT_OUT_DIR)
        )
        self.image_output_name_var.set(
            str(data.get("output_name") or image_runner.DEFAULT_OUTPUT_NAME)
        )
        self.image_save_var.set(bool(data.get("save_image", True)))
        self.image_print_payload_var.set(bool(data.get("print_payload")))
        self.image_prompt_text.delete("1.0", tk.END)
        self.image_prompt_text.insert(
            "1.0", str(data.get("prompt") or image_runner.DEFAULT_PROMPT)
        )
        self.image_url_text.delete("1.0", tk.END)
        self.image_url_text.insert("1.0", "\n".join(data.get("image_url") or []))
        self.image_file_list.delete(0, tk.END)
        for item in data.get("image_file") or []:
            self.image_file_list.insert(tk.END, str(item))

    def _save_persisted_configs(self) -> None:
        self.config_store.save("video", self._video_settings())
        self.config_store.save("image", self._image_settings())

    def _load_persisted_configs(self) -> None:
        try:
            video_data = self.config_store.load("video")
            if video_data is not None:
                self._apply_video_settings(video_data)
            image_data = self.config_store.load("image")
            if image_data is not None:
                self._apply_image_settings(image_data)
        except Exception as exc:
            messagebox.showwarning(
                "Config load warning", f"Failed to load saved config: {exc}"
            )

    def _on_close(self) -> None:
        try:
            self._save_persisted_configs()
        except Exception as exc:
            messagebox.showwarning(
                "Config save warning", f"Failed to save config: {exc}"
            )
        self.root.destroy()

    def save_video_config(self) -> None:
        try:
            self.config_store.save("video", self._video_settings())
        except Exception as exc:
            messagebox.showwarning(
                "Auto save warning", f"Failed to update SQLite config: {exc}"
            )
        self._save_config(self._video_settings(), "Save video config")

    def save_image_config(self) -> None:
        try:
            self.config_store.save("image", self._image_settings())
        except Exception as exc:
            messagebox.showwarning(
                "Auto save warning", f"Failed to update SQLite config: {exc}"
            )
        self._save_config(self._image_settings(), "Save image config")

    def _save_config(self, data: dict, title: str) -> None:
        try:
            path = filedialog.asksaveasfilename(
                title=title,
                defaultextension=".json",
                filetypes=[("JSON", "*.json"), ("All Files", "*.*")],
            )
            if not path:
                return
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def load_video_config(self) -> None:
        data = self._load_config("Load video config")
        if data is None:
            return
        self._apply_video_settings(data)
        try:
            self.config_store.save("video", self._video_settings())
        except Exception as exc:
            messagebox.showwarning(
                "Auto save warning", f"Failed to update SQLite config: {exc}"
            )

    def load_image_config(self) -> None:
        data = self._load_config("Load image config")
        if data is None:
            return
        self._apply_image_settings(data)
        try:
            self.config_store.save("image", self._image_settings())
        except Exception as exc:
            messagebox.showwarning(
                "Auto save warning", f"Failed to update SQLite config: {exc}"
            )

    def _load_config(self, title: str) -> dict | None:
        try:
            path = filedialog.askopenfilename(
                title=title, filetypes=[("JSON", "*.json"), ("All Files", "*.*")]
            )
            if not path:
                return None
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise RuntimeError("config file must contain a JSON object")
            return data
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return None

    def start_video_test(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A test is already running")
            return
        try:
            settings = self._video_settings()
        except Exception as exc:
            messagebox.showerror("Invalid video settings", str(exc))
            return
        try:
            self.config_store.save("video", settings)
        except Exception as exc:
            messagebox.showwarning(
                "Auto save warning", f"Failed to update SQLite config: {exc}"
            )
        self.video_log_text.configure(state="normal")
        self.video_log_text.delete("1.0", tk.END)
        self.video_log_text.configure(state="disabled")
        self.video_status_var.set("Running...")
        self.worker = threading.Thread(
            target=self._run_video_test, args=(settings,), daemon=True
        )
        self.worker.start()

    def start_image_test(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A test is already running")
            return
        try:
            settings = self._image_settings()
        except Exception as exc:
            messagebox.showerror("Invalid image settings", str(exc))
            return
        try:
            self.config_store.save("image", settings)
        except Exception as exc:
            messagebox.showwarning(
                "Auto save warning", f"Failed to update SQLite config: {exc}"
            )
        self.image_log_text.configure(state="normal")
        self.image_log_text.delete("1.0", tk.END)
        self.image_log_text.configure(state="disabled")
        self.image_preview_label.configure(image="", text="No image yet")
        self.preview_image = None
        self.image_status_var.set("Running...")
        self.worker = threading.Thread(
            target=self._run_image_test, args=(settings,), daemon=True
        )
        self.worker.start()

    def _run_video_test(self, settings: dict) -> None:
        writer = QueueWriter(lambda text: self._video_log(text))
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                payload = video_runner.build_payload(settings)
                if settings["print_payload"]:
                    print("[payload]")
                    print(video_runner.pretty(payload))
                create_data = video_runner.create_task(settings, payload)
                task_id = video_runner.extract_task_id(create_data)
                result = video_runner.poll_task(settings, task_id)
                status = str(result.get("status") or "").strip().lower()
                if status != "completed":
                    self.log_queue.put(("video_status", "Task failed"))
                    return
                media_url = video_runner.resolve_media_url(result)
                print(f"[result] media_url={media_url}")
                if not settings["skip_head_check"]:
                    video_runner.head_check_media(
                        media_url, int(settings["request_timeout"])
                    )
                if settings["download_check"]:
                    saved = video_runner.download_media(
                        media_url,
                        settings["out_dir"],
                        settings["output_name"],
                        int(settings["request_timeout"]),
                    )
                    print(f"[result] saved={saved}")
            self.log_queue.put(("video_status", "Completed"))
        except Exception as exc:
            self._video_log(f"[error] {exc}\n")
            self.log_queue.put(("video_status", "Failed"))
        finally:
            self.log_queue.put(("worker_done", None))

    def _run_image_test(self, settings: dict) -> None:
        writer = QueueWriter(lambda text: self._image_log(text))
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                payload = image_runner.build_payload(settings)
                if settings["print_payload"]:
                    print("[payload]")
                    print(image_runner.pretty(payload))
                data = image_runner.create_image(settings, payload)
                image_bytes, mime_type = image_runner.extract_inline_image(data)
                print(f"[result] mime_type={mime_type} bytes={len(image_bytes)}")
                if settings["save_image"]:
                    saved = image_runner.save_image_bytes(
                        image_bytes,
                        mime_type,
                        settings["out_dir"],
                        settings["output_name"],
                    )
                    print(f"[result] saved={saved}")
                self.log_queue.put(("image_preview", image_bytes))
            self.log_queue.put(("image_status", "Completed"))
        except Exception as exc:
            self._image_log(f"[error] {exc}\n")
            self.log_queue.put(("image_status", "Failed"))
        finally:
            self.log_queue.put(("worker_done", None))


def main() -> None:
    root = tk.Tk()
    MediaTestGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
