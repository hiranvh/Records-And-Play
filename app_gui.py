import customtkinter as ctk
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import agent_engine
import web_core

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.mock_server_process = None

        self.title("AI-Driven Automation Agent - Commander")
        self.geometry("800x600")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Log Frame (Shows Thoughts & Actions)
        self.log_frame = ctk.CTkScrollableFrame(self, label_text="Agent Logs")
        self.log_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="nsew")

        # Input Frame
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="ew")
        self.input_frame.grid_columnconfigure(0, weight=1)
        self.input_frame.grid_columnconfigure(1, weight=1)

        self.url_entry = ctk.CTkEntry(self.input_frame, placeholder_text="Enter target URL (e.g. https://example.com/form)")
        self.url_entry.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="ew")
        self.url_entry.insert(0, "http://localhost:5000")

        self.chat_entry = ctk.CTkEntry(self.input_frame, placeholder_text="Enter intent for replay (optional)...")
        self.chat_entry.grid(row=1, column=0, padx=10, pady=10, sticky="ew")
        self.chat_entry.bind("<Return>", self.send_command)

        self.send_btn = ctk.CTkButton(self.input_frame, text="Execute", command=self.send_command)
        self.send_btn.grid(row=1, column=1, padx=10, pady=10)

        self.record_btn = ctk.CTkButton(self.input_frame, text="Record", fg_color="orange", hover_color="darkorange", command=self.run_teaching_mode)
        self.record_btn.grid(row=1, column=2, padx=10, pady=10)

        self.replay_btn = ctk.CTkButton(self.input_frame, text="Replay", fg_color="seagreen", hover_color="darkgreen", command=self.replay_workflow)
        self.replay_btn.grid(row=1, column=3, padx=(0, 10), pady=10)
        
        self.status_label = ctk.CTkLabel(self.input_frame, text="Ready", text_color="green")
        self.status_label.grid(row=2, column=0, columnspan=4, pady=5)

        # Initialize Engine
        self.engine = agent_engine.AgentEngine(update_callback=self.update_log)
        
        # We load the model in the background so the UI doesn't freeze
        self.update_log("GUI Started. Waiting for Model load...")
        threading.Thread(target=self._load_model_in_background, daemon=True).start()

    def _load_model_in_background(self):
        try:
            self.engine.load_model()
            self.set_status("Model Ready", "green")
        except Exception as exc:
            self.update_log(f"Model load unavailable: {exc}")
            self.set_status("Model Unavailable", "orange")

    def update_log(self, text):
        def insert_text():
            lbl = ctk.CTkLabel(self.log_frame, text=text, anchor="w", justify="left", wraplength=700)
            lbl.pack(fill="x", pady=2, padx=5)
            # Update scroll via internal widget update
            self.log_frame._parent_canvas.yview_moveto(1)
        # Use after() to ensure we update from the main thread
        self.after(0, insert_text)

    def set_status(self, text, color="white"):
        self.status_label.configure(text=text, text_color=color)

    def _is_server_available(self, url):
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except (urllib.error.URLError, TimeoutError):
            return False

    def _get_target_url(self):
        url = self.url_entry.get().strip()
        if not url:
            self.update_log("Please enter a target URL before recording or replaying.")
            self.set_status("URL Required", "red")
            return None

        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
            self.url_entry.delete(0, 'end')
            self.url_entry.insert(0, url)

        return url

    def _ensure_mock_server(self, url):
        if "localhost:5000" not in url and "127.0.0.1:5000" not in url:
            return True

        if self._is_server_available(url):
            return True

        if self.mock_server_process and self.mock_server_process.poll() is None:
            for _ in range(10):
                if self._is_server_available(url):
                    return True
                time.sleep(0.5)
            return False

        self.update_log("Starting local mock server on http://localhost:5000 ...")
        self.mock_server_process = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(__file__), "mock_server.py")],
            cwd=os.path.dirname(__file__),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(20):
            if self._is_server_available(url):
                self.update_log("Mock server is ready.")
                return True
            if self.mock_server_process.poll() is not None:
                break
            time.sleep(0.5)

        self.update_log("Unable to start the local mock server.")
        return False

    def send_command(self, event=None):
        cmd = self.chat_entry.get()
        if not cmd.strip():
            self.replay_workflow()
            return

        target_url = self._get_target_url()
        if not target_url:
            return
            
        self.chat_entry.delete(0, 'end')
        self.update_log(f"--- USER: {cmd} ---")
        self.update_log(f"Target URL: {target_url}")
        self.set_status("Executing Agent Workflow...", "yellow")

        def task():
            try:
                if not self._ensure_mock_server(target_url):
                    self.set_status("Mock Server Unavailable", "red")
                    return
                self.engine.execute_workflow(cmd, target_url=target_url)
                self.set_status("Execution Complete", "green")
            except Exception as e:
                self.update_log(f"ERROR: {str(e)}")
                self.set_status("Execution Failed", "red")
                
        threading.Thread(target=task, daemon=True).start()

    def run_teaching_mode(self):
        target_url = self._get_target_url()
        if not target_url:
            return

        self.update_log(f"--- Starting Recording on {target_url} ---")
        self.set_status("Recording Workflow...", "orange")

        def task():
            try:
                if not self._ensure_mock_server(target_url):
                    self.set_status("Mock Server Unavailable", "red")
                    return
                web_core.start_teaching_mode(target_url)
                self.update_log("Recording Finished.")
                self.set_status("Ready", "green")
            except Exception as exc:
                self.update_log(f"Recording failed: {exc}")
                self.set_status("Recording Failed", "red")

        threading.Thread(target=task, daemon=True).start()

    def replay_workflow(self):
        target_url = self._get_target_url()
        if not target_url:
            return

        self.update_log(f"--- Replaying recorded workflow on {target_url} ---")
        self.set_status("Replaying Workflow...", "yellow")

        def task():
            try:
                if not self._ensure_mock_server(target_url):
                    self.set_status("Mock Server Unavailable", "red")
                    return

                success, msg, screenshot = web_core.run_execution_mode(target_url, headless=False)
                if success:
                    self.update_log(f"Replay finished. Screenshot saved to {screenshot}")
                    self.set_status("Replay Complete", "green")
                else:
                    self.update_log(msg)
                    self.set_status("Replay Failed", "red")
            except Exception as exc:
                self.update_log(f"Replay failed: {exc}")
                self.set_status("Replay Failed", "red")

        threading.Thread(target=task, daemon=True).start()

    def on_close(self):
        if self.mock_server_process and self.mock_server_process.poll() is None:
            self.mock_server_process.terminate()
        self.destroy()

if __name__ == "__main__":
    app = App()
    app.mainloop()
