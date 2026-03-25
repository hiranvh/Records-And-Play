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
        self.geometry("1000x750")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(0, lambda: self._maximize_window(self))

        # Layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Left Menu Panel
        self.sidebar_frame = ctk.CTkFrame(self, width=280, corner_radius=0, fg_color="#1E293B")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(5, weight=1)
        
        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="⚡ AGENT\nCOMMANDER", font=ctk.CTkFont(size=24, weight="bold"), text_color="#38BDF8")
        self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 20))
        
        self.url_label = ctk.CTkLabel(self.sidebar_frame, text="TARGET ORG URL", font=ctk.CTkFont(size=11, weight="bold"), text_color="#94A3B8")
        self.url_label.grid(row=1, column=0, padx=20, pady=(10, 0), sticky="w")
        self.url_entry = ctk.CTkEntry(self.sidebar_frame, height=35, placeholder_text="https://example.com", border_color="#475569")
        self.url_entry.grid(row=2, column=0, padx=20, pady=(5, 20), sticky="ew")
        self.url_entry.insert(0, self.get_default_url())
        
        self.workflow_label = ctk.CTkLabel(self.sidebar_frame, text="WORKFLOWS", font=ctk.CTkFont(size=11, weight="bold"), text_color="#94A3B8")
        self.workflow_label.grid(row=3, column=0, padx=20, pady=(10, 0), sticky="w")

        self.selected_workflow_var = ctk.StringVar(value="workflow.json")
        self.selected_workflow_var.trace_add("write", self.load_fixed_rules)
        
        self.workflow_list_frame = ctk.CTkScrollableFrame(self.sidebar_frame, height=200, fg_color="#0F172A", corner_radius=5)
        self.workflow_list_frame.grid(row=4, column=0, padx=15, pady=(5, 10), sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        self.rules_label = ctk.CTkLabel(self.sidebar_frame, text="FIXED AI RULES", font=ctk.CTkFont(size=11, weight="bold"), text_color="#94A3B8")
        self.rules_label.grid(row=5, column=0, padx=20, pady=(5, 0), sticky="w")
        
        self.rules_textbox = ctk.CTkTextbox(self.sidebar_frame, height=80, font=ctk.CTkFont(size=12), fg_color="#0F172A", border_color="#334155", border_width=1, wrap="word")
        self.rules_textbox.grid(row=6, column=0, padx=15, pady=(5, 5), sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(6, weight=0)

        self.save_rules_btn = ctk.CTkButton(self.sidebar_frame, text="Save Rules", height=24, width=80, font=ctk.CTkFont(size=11, weight="bold"), fg_color="#475569", hover_color="#334155", command=self.save_fixed_rules)
        self.save_rules_btn.grid(row=7, column=0, padx=15, pady=(0, 10), sticky="e")

        self.status_label = ctk.CTkLabel(self.sidebar_frame, text="Ready", text_color="#10B981", font=ctk.CTkFont(size=14, weight="bold"))
        self.status_label.grid(row=8, column=0, padx=20, pady=20, sticky="s")
        self.sidebar_frame.grid_rowconfigure(8, weight=0)

        # Right Main Panel
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)
        
        # Log Console Container (Selectable)
        self.log_container = ctk.CTkFrame(self.main_frame, border_width=2, border_color="#334155", fg_color="#0F172A")
        self.log_container.grid(row=0, column=0, sticky="nsew")
        self.log_container.grid_rowconfigure(1, weight=1)
        self.log_container.grid_columnconfigure(0, weight=1)

        self.log_header = ctk.CTkLabel(self.log_container, text="Agent Console", font=ctk.CTkFont(size=16, weight="bold"), text_color="#E2E8F0")
        self.log_header.grid(row=0, column=0, pady=(10, 0), padx=15, sticky="w")

        self.log_box = ctk.CTkTextbox(self.log_container, font=ctk.CTkFont(family="Consolas", size=13), fg_color="transparent", text_color="#E2E8F0", wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 5))
        
        # Color tags for console text
        self.log_box.tag_config("ERROR", foreground="#EF4444")
        self.log_box.tag_config("SUCCESS", foreground="#10B981")
        self.log_box.tag_config("USER", foreground="#38BDF8")
        self.log_box.tag_config("WARNING", foreground="#F59E0B")
        self.log_box.tag_config("SYSTEM", foreground="#94A3B8")
        
        self.log_box.configure(state="disabled")
        
        # Action Bar (Bottom)
        self.action_frame = ctk.CTkFrame(self.main_frame, height=80, fg_color="#1E293B", corner_radius=10)
        self.action_frame.grid(row=1, column=0, pady=(20, 0), sticky="ew")
        self.action_frame.grid_columnconfigure(0, weight=1)
        
        self.chat_entry = ctk.CTkEntry(self.action_frame, height=45, placeholder_text="Tell the agent what to automate (e.g. 'Create 5 new users')...", font=ctk.CTkFont(size=14), border_color="#475569")
        self.chat_entry.grid(row=0, column=0, padx=(15, 10), pady=15, sticky="ew")
        self.chat_entry.bind("<Return>", self.send_command)
        
        self.send_btn = ctk.CTkButton(self.action_frame, height=45, text="🪄 Execute Agent", font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2563EB", hover_color="#1D4ED8", command=self.send_command)
        self.send_btn.grid(row=0, column=1, padx=5, pady=15)
        
        self.replay_btn = ctk.CTkButton(self.action_frame, height=45, width=100, text="▶ Replay", font=ctk.CTkFont(size=14, weight="bold"), fg_color="#059669", hover_color="#047857", command=self.replay_workflow)
        self.replay_btn.grid(row=0, column=2, padx=5, pady=15)
        
        self.record_btn = ctk.CTkButton(self.action_frame, height=45, width=100, text="● Record", font=ctk.CTkFont(size=14, weight="bold"), fg_color="#D97706", hover_color="#B45309", command=self.run_teaching_mode)
        self.record_btn.grid(row=0, column=3, padx=5, pady=15)
        
        self.stop_btn = ctk.CTkButton(self.action_frame, height=45, width=100, text="🛑 Stop", font=ctk.CTkFont(size=14, weight="bold"), fg_color="#EF4444", hover_color="#DC2626", command=self.stop_execution)
        self.stop_btn.grid(row=0, column=4, padx=(5, 15), pady=15)

        # After initialization, load actual workflows:
        self.refresh_workflow_list()

        # Initialize Engine
        self.engine = agent_engine.AgentEngine(update_callback=self.update_log)
        
        # We load the model in the background so the UI doesn't freeze
        self.update_log("GUI Started. Waiting for Model load...")
        threading.Thread(target=self._load_model_in_background, daemon=True).start()

    def _maximize_window(self, window):
        try:
            window.state("zoomed")
            return
        except Exception:
            pass

        try:
            window.attributes("-zoomed", True)
            return
        except Exception:
            pass

        try:
            window.update_idletasks()
            width = window.winfo_screenwidth()
            height = window.winfo_screenheight()
            window.geometry(f"{width}x{height}+0+0")
        except Exception:
            pass

    def refresh_workflow_list(self):
        for widget in self.workflow_list_frame.winfo_children():
            widget.destroy()
            
        workflows = self.get_workflows()
        if not workflows: workflows = ["workflow.json"]
            
        if self.selected_workflow_var.get() not in workflows:
            self.selected_workflow_var.set(workflows[0])
            
        for wf in workflows:
            row_frame = ctk.CTkFrame(self.workflow_list_frame, fg_color="transparent")
            row_frame.pack(fill="x", pady=4)
            
            rb = ctk.CTkRadioButton(row_frame, text=wf.replace('.json',''), variable=self.selected_workflow_var, value=wf, font=ctk.CTkFont(size=12))
            rb.pack(side="left", padx=2, fill="x", expand=True)
            
            del_btn = ctk.CTkButton(row_frame, text="🗑️", width=25, height=25, fg_color="#EF4444", hover_color="#DC2626", command=lambda w=wf: self.delete_workflow(w))
            del_btn.pack(side="right", padx=2)

            clean_btn = ctk.CTkButton(row_frame, text="🧹", width=25, height=25, fg_color="#0284C7", hover_color="#0369A1", command=lambda w=wf: self.compact_workflow(w))
            clean_btn.pack(side="right", padx=2)
            
            edit_btn = ctk.CTkButton(row_frame, text="✏️", width=25, height=25, fg_color="#475569", hover_color="#334155", command=lambda w=wf: self.edit_workflow(w))
            edit_btn.pack(side="right", padx=2)

    def delete_workflow(self, wf_name):
        workflows_dir = os.path.join(os.path.dirname(__file__), "workflows")
        path = os.path.join(workflows_dir, wf_name)
        if os.path.exists(path):
            os.remove(path)
            self.update_log(f"Deleted workflow: {wf_name}")
            self.refresh_workflow_list()

    def compact_workflow(self, wf_name=None):
        if not wf_name:
            wf_name = self.selected_workflow_var.get()
        if not wf_name:
            self.update_log("No workflow selected to compact.")
            return

        if not wf_name.endswith('.json'):
            wf_name += '.json'

        try:
            result = web_core.compact_workflow(wf_name)
            before_count = result["before_count"]
            after_count = result["after_count"]
            if before_count == after_count:
                self.update_log(f"Workflow {wf_name} is already compact: {after_count} steps.")
            else:
                self.update_log(f"Compacted {wf_name}: {before_count} raw events -> {after_count} workflow steps.")
            self.refresh_workflow_list()
        except Exception as exc:
            self.update_log(f"Unable to compact {wf_name}: {exc}")

    def load_fixed_rules(self, *args):
        wf_name = self.selected_workflow_var.get()
        if not wf_name: return
        import json
        if not wf_name.endswith('.json'): wf_name += '.json'
        path = os.path.join(os.path.dirname(__file__), "workflows", wf_name)
        rules = ""
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    rules = data.get("fixed_rules", "")
            except: pass
        self.rules_textbox.delete("1.0", "end")
        if rules:
            self.rules_textbox.insert("1.0", rules)
            
    def save_fixed_rules(self):
        wf_name = self.selected_workflow_var.get()
        if not wf_name: return
        import json
        if not wf_name.endswith('.json'): wf_name += '.json'
        path = os.path.join(os.path.dirname(__file__), "workflows", wf_name)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data["fixed_rules"] = self.rules_textbox.get("1.0", "end-1c").strip()
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                self.update_log(f"Saved fixed rules to {wf_name}")
            except Exception as e:
                self.update_log(f"Error saving rules: {e}")
                
    def get_workflows(self):
        workflows_dir = os.path.join(os.path.dirname(__file__), "workflows")
        if not os.path.exists(workflows_dir):
            return ["workflow.json"]
        files = [f for f in os.listdir(workflows_dir) if f.endswith('.json')]
        return files if files else ["workflow.json"]

    def get_default_url(self):
        config_path = os.path.join(os.path.dirname(__file__), "config.properties")
        if os.path.exists(config_path):
            import configparser
            config = configparser.ConfigParser()
            config.read(config_path)
            sections = config.sections()
            if sections:
                return sections[0]
        return "http://localhost:5000"

    def edit_workflow(self, wf_name=None):
        if not wf_name: wf_name = self.selected_workflow_var.get()
        import json
        workflows_dir = os.path.join(os.path.dirname(__file__), "workflows")
        path = os.path.join(workflows_dir, wf_name) if wf_name.endswith('.json') else os.path.join(workflows_dir, wf_name + '.json')
            
        if not os.path.exists(path):
            self.update_log(f"Workflow '{wf_name}' does not exist yet to edit. Please Record it.")
            return
            
        edit_win = ctk.CTkToplevel(self)
        edit_win.title(f"Editing {wf_name}")
        edit_win.geometry("600x500")
        self.after(0, lambda: self._maximize_window(edit_win))
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        textbox = ctk.CTkTextbox(edit_win, width=580, height=400)
        textbox.pack(padx=10, pady=10)
        textbox.insert("1.0", json.dumps(data, indent=4))
        
        def save():
            try:
                new_data = json.loads(textbox.get("1.0", "end"))
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(new_data, f, indent=4)
                self.update_log(f"Successfully updated {wf_name}")
                edit_win.destroy()
            except json.JSONDecodeError as e:
                self.update_log(f"Invalid JSON: {e}")
                
        save_btn = ctk.CTkButton(edit_win, text="Save Changes", command=save)
        save_btn.pack(pady=10)

    def _load_model_in_background(self):
        try:
            self.engine.load_model()
            self.set_status("Model Ready", "green")
        except Exception as exc:
            self.update_log(f"Model load unavailable: {exc}")
            self.set_status("Model Unavailable", "orange")

    def update_log(self, text, log_type=None):
        def insert_text():
            self.log_box.configure(state="normal")
            
            # Simple heuristic for tags if log_type is not provided
            tag_to_use = log_type
            if not tag_to_use:
                if "ERROR" in text or "fail" in text.lower() or "Unable to" in text:
                    tag_to_use = "ERROR"
                elif "SUCCESS" in text or "Complete" in text or "Finished" in text or "ready" in text.lower():
                    tag_to_use = "SUCCESS"
                elif "USER:" in text:
                    tag_to_use = "USER"
                elif "Warning" in text or "Waiting" in text or "Starting" in text:
                    tag_to_use = "WARNING"
                else:
                    tag_to_use = "SYSTEM"
            
            timestamp = time.strftime("%H:%M:%S")
            formatted_text = f"[{timestamp}] {text}\n"
            
            self.log_box.insert("end", formatted_text, tag_to_use)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
            
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
        cmd = self.chat_entry.get().strip()
        fixed_rules = self.rules_textbox.get("1.0", "end-1c").strip()
        
        if not cmd and not fixed_rules:
            self.replay_workflow()
            return
            
        final_cmd = cmd
        if fixed_rules:
            if final_cmd:
                final_cmd = f"{final_cmd}. Also follow these fixed workflow rules: {fixed_rules}"
            else:
                final_cmd = f"Follow these fixed workflow rules: {fixed_rules}"

        target_url = self._get_target_url()
        if not target_url:
            return
            
        self.chat_entry.delete(0, 'end')
        self.update_log(f"--- USER (With Rules): {final_cmd} ---" if fixed_rules else f"--- USER: {final_cmd} ---")
        self.update_log(f"Target URL: {target_url}")
        self.set_status("Executing Agent Workflow...", "yellow")

        def task():
            import web_core
            if hasattr(web_core, 'stop_execution_event'):
                web_core.stop_execution_event.clear()
            try:
                if not self._ensure_mock_server(target_url):
                    self.set_status("Mock Server Unavailable", "red")
                    return
                wf_name = self.selected_workflow_var.get()
                if not wf_name.endswith('.json'): wf_name += '.json'
                self.engine.execute_workflow(final_cmd, target_url=target_url, workflow_name=wf_name)
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

        def set_button_recording_state(is_recording):
            if is_recording:
                self.record_btn.configure(text="■ Stop", fg_color="#DC2626", hover_color="#991B1B", command=self.stop_recording)
            else:
                self.record_btn.configure(text="● Record", fg_color="#D97706", hover_color="#B45309", command=self.run_teaching_mode)

        def task():
            self.after(0, lambda: set_button_recording_state(True))
            try:
                if not self._ensure_mock_server(target_url):
                    self.set_status("Mock Server Unavailable", "red")
                    self.after(0, lambda: set_button_recording_state(False))
                    return
                wf_name = self.selected_workflow_var.get()
                if not wf_name.endswith('.json'): wf_name += '.json'
                captured_steps = web_core.start_teaching_mode(target_url, workflow_name=wf_name)
                
                self.set_status("Ready", "green")
                self.after(0, lambda: self.prompt_save_recording(captured_steps, target_url))
            except Exception as exc:
                self.update_log(f"Recording failed: {exc}")
                self.set_status("Recording Failed", "red")
            finally:
                self.after(0, lambda: set_button_recording_state(False))

        threading.Thread(target=task, daemon=True).start()

    def prompt_save_recording(self, steps, url):
        if not steps:
            self.update_log("No steps were recorded. Discarding.")
            return

        normalized_steps = web_core._normalize_workflow_steps(steps)
            
        save_win = ctk.CTkToplevel(self)
        save_win.title("Save Recording")
        save_win.geometry("400x250")
        self.after(0, lambda: self._maximize_window(save_win))
        save_win.transient(self) # Keep on top of main window
        save_win.grab_set() # Make modal
        
        lbl = ctk.CTkLabel(
            save_win,
            text=(
                f"Captured {len(steps)} raw events.\n"
                f"Compacted to {len(normalized_steps)} workflow steps.\n"
                "What would you like to name this workflow?"
            ),
            font=ctk.CTkFont(size=14),
        )
        lbl.pack(pady=20)
        
        name_entry = ctk.CTkEntry(save_win, width=250, placeholder_text="e.g. login_flow")
        name_entry.pack(pady=10)
        name_entry.insert(0, self.selected_workflow_var.get().replace('.json', ''))
        
        def on_save():
            name = name_entry.get().strip()
            if not name:
                name = "workflow"
            if not name.endswith(".json"):
                name += ".json"
            
            web_core.save_workflow(url, steps, name)
            self.update_log(
                f"Successfully saved {len(normalized_steps)} workflow steps to {name} "
                f"(compacted from {len(steps)} raw events)"
            )
            self.selected_workflow_var.set(name)
            self.refresh_workflow_list()
            save_win.destroy()
            
        def on_discard():
            self.update_log("Recording discarded by user.")
            save_win.destroy()
            
        btn_frame = ctk.CTkFrame(save_win, fg_color="transparent")
        btn_frame.pack(pady=20)
        
        ctk.CTkButton(btn_frame, text="Save", fg_color="#10B981", hover_color="#059669", width=100, command=on_save).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Discard", fg_color="#EF4444", hover_color="#DC2626", width=100, command=on_discard).pack(side="left", padx=10)

    def stop_execution(self):
        import web_core
        if hasattr(web_core, 'stop_execution_event'):
            web_core.stop_execution_event.set()
        if hasattr(web_core, 'stop_recording_event'):
            web_core.stop_recording_event.set()
        self.update_log("Stop signal sent. Stopping on next step...")
        self.set_status("Stopping...", "orange")

    def stop_recording(self):
        import web_core
        if hasattr(web_core, 'stop_recording_event'):
            web_core.stop_recording_event.set()
            self.update_log("Stopping recording...")

    def replay_workflow(self):
        target_url = self._get_target_url()
        if not target_url:
            return

        self.update_log(f"--- Replaying recorded workflow on {target_url} ---")
        self.set_status("Replaying Workflow...", "yellow")

        def task():
            import web_core
            if hasattr(web_core, 'stop_execution_event'):
                web_core.stop_execution_event.clear()
            try:
                if not self._ensure_mock_server(target_url):
                    self.set_status("Mock Server Unavailable", "red")
                    return

                wf_name = self.selected_workflow_var.get()
                if not wf_name.endswith('.json'): wf_name += '.json'
                success, msg, screenshot, _ = web_core.run_execution_mode(target_url, headless=False, workflow_name=wf_name)
                if success:
                    self.update_log(f"Replay finished. Execution logged to Excel (.xlsx) & Screenshot saved to {screenshot}")
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
