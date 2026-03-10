import json
import os
from pydantic import BaseModel, Field
import models_manager
import web_core
import verification

# Define the structured output we want from the LLM
class ActionIntent(BaseModel):
    action: str = Field(description="The automation action to perform (e.g., 'create_enrollment')")
    count: int = Field(description="Number of times to repeat the action. Default to 1.", default=1)
    extracted_data: list[dict] = Field(description="List of dictionaries containing extracted key-value pairs (e.g., [{'First Name': 'Alice', 'SSN': '123'}])", default_factory=list)

class AgentEngine:
    def __init__(self, update_callback=None):
        """
        update_callback is an optional function that takes a string message to display in the GUI.
        """
        self.update_callback = update_callback
        self.llm = None
        self._last_download_pct = -10
        self._log("Initializing Engine...")

    def _log(self, msg):
        print(msg)
        if self.update_callback:
            self.update_callback(msg)

    def load_model(self):
        try:
            from llama_cpp import Llama
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "llama-cpp-python is not installed. Install it with a prebuilt wheel or add Visual C++ build tools before using the agent model."
            ) from exc

        self._log("Checking/downloading Phi-3.5 model...")
        models_manager.ensure_model_exists(progress_callback=self._download_progress)
        model_path = models_manager.get_model_path()
        self._log("Loading model into memory...")
        # Load local LLM optimized for CPU
        self.llm = Llama(
            model_path=model_path,
            n_ctx=2048,
            n_threads=4,
            verbose=False
        )
        self._log("Model ready.")

    def _download_progress(self, current, total):
        pct = (current / total) * 100
        rounded_pct = int(pct // 10) * 10
        if rounded_pct > self._last_download_pct and rounded_pct <= 100:
            self._last_download_pct = rounded_pct
            self._log(f"Downloading Model... {pct:.1f}%")

    def coordinator_agent(self, user_command):
        """
        The Coordinator parsing an unstructured natural language command.
        Uses Phi-3.5 with strict instruction formatting to output JSON.
        """
        self._log("Coordinator Agent: Parsing user intent...")
        
        prompt = f"""<|system|>
You are the Coordinator Agent. Your job is to parse the user's request into a reliable JSON format. 
Extract the requested action, the number of repetitions (count), and any specific data provided.
DO NOT output anything other than raw valid JSON.

Format Required:
{{
  "action": "create_enrollments",
  "count": 1,
  "extracted_data": [
      {{"ssn": "value", "first_name": "value", "last_name": "value"}}
  ]
}}
<|user|>
User Command: "{user_command}"
<|assistant|>
"""
        response = self.llm(
            prompt,
            max_tokens=256,
            stop=["<|end|>", "<|user|>"],
            echo=False
        )
        output_text = response['choices'][0]['text'].strip()
        
        try:
            # Basic cleanup in case model wrapped it in ```json
            if "**" in output_text: output_text = output_text.replace("**", "")
            if "```json" in output_text:
                output_text = output_text.split("```json")[1].split("```")[0].strip()
            elif "```" in output_text:
                output_text = output_text.split("```")[1].split("```")[0].strip()
                
            parsed = json.loads(output_text)
            intent = ActionIntent(**parsed)
            
            self._log(f"Coordinator parsed intent: Action='{intent.action}', Count={intent.count}, Records={len(intent.extracted_data)}")
            return intent
        except Exception as e:
            self._log(f"Coordinator parsed error: \nRaw Output: {output_text}\nError:{str(e)}")
            return None

    def execute_workflow(self, user_command, target_url=None, custom_data=None):
        if not self.llm:
            try:
                self.load_model()
            except Exception as e:
                self._log(f"Error loading model: {e}")
                return

        intent = self.coordinator_agent(user_command)
        if not intent:
            self._log("Task cancelled - failed to parse intent.")
            return

        # Handle repetitions
        total = intent.count
        self._log(f"Orchestrating {total} loop(s) for task '{intent.action}'")

        # Determine data array
        data_to_loop = intent.extracted_data
        
        # If user passed custom_data via GUI (like a CSV or pasted block), we append or override it.
        if custom_data and isinstance(custom_data, list):
            data_to_loop.extend(custom_data)
        
        if len(data_to_loop) < total:
             # LLM didn't provide enough specific data, so we loop over empty dicts to let automation try auto-fill
             data_to_loop.extend([{}] * (total - len(data_to_loop)))
             

        # Loop execution
        for i in range(total):
            record_data = data_to_loop[i]
            self._log(f"\n--- Starting automation run {i+1} of {total} ---")
            self._log(f"Web Automation Agent: Executing Playwright task with data: {record_data}")
            
            # 1. Run Web Automation
            # We assume a Teaching Mode workflow exists.
            success, msg, screenshot = web_core.run_execution_mode(target_url, override_data=record_data, headless=False)
            if not success:
                self._log(f"Web Automation Failed: {msg}")
                continue
            
            self._log("Web Automation completed. Firing Data & Verification Agent...")
            
            # 2. Run Data Verification
            # We assume the action created a Customer and SSN is primary key
            ssn_used = record_data.get('ssn', 'unknown')
            # To be truly flexible we should query the Db without hardcoding SSN, but for the mock we check SSN.
            import db_mock
            match_success, v_msg, row = db_mock.verify_customer_creation(ssn_used)
            
            # 3. Generate Report
            report_path = verification.generate_report(screenshot, row, ssn_used, match_success, v_msg)
            
            if match_success:
                self._log(f"Verification Agent: Match SUCCESS! Report saved to {report_path}")
            else:
                self._log(f"Verification Agent: Match FAILED: {v_msg}. Report saved to {report_path}")

        self._log("\nAll tasks completed.")
