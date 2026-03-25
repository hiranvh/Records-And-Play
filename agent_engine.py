import json
import os
import re
import threading
from pydantic import BaseModel, Field
import models_manager
import web_core
import verification

try:
    from langchain_core.prompts import PromptTemplate
    from langchain_core.output_parsers import PydanticOutputParser
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

class ActionIntent(BaseModel):
    action: str = Field(description="The automation action to perform (e.g., 'create_enrollment')")
    count: int = Field(description="Number of times to repeat the action. Default to 1.", default=1)
    extracted_data: list[dict] = Field(
        description="List of dictionaries containing extracted key-value pairs (e.g., [{'First Name': 'Alice', 'SSN': '123'}])",
        default_factory=list,
    )

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

class AgentEngine:
    def __init__(self, update_callback=None):
        """
        update_callback is an optional function that takes a string message to display in the GUI.
        """
        self.update_callback = update_callback
        self.llm = None
        self._last_download_pct = -10
        self._model_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._workflow_fields_cache = {}
        self._log("Initializing Engine...")

    def _log(self, msg):
        print(msg)
        if self.update_callback:
            self.update_callback(msg)

    def load_model(self):
        if self.llm is not None:
            return

        with self._model_lock:
            if self.llm is not None:
                return

            if LANGCHAIN_AVAILABLE:
                try:
                    from langchain_community.llms import LlamaCpp
                except ModuleNotFoundError as exc:
                    raise RuntimeError(
                        "langchain-community or llama-cpp-python is not installed."
                    ) from exc

                cpu_count = os.cpu_count() or 4
                thread_count = max(2, min(8, cpu_count - 1))

                self._log("Checking/downloading Phi-3.5 model...")
                models_manager.ensure_model_exists(progress_callback=self._download_progress)
                
                # Since models_manager might return differently, let's get the standard path
                # Need to get model_path. Previously it used models_manager.get_model_path()
                # Let's ensure models_manager.MODEL_PATH is used or get_model_path if exists.
                try:
                    model_path = models_manager.get_model_path()
                except AttributeError:
                    model_path = models_manager.MODEL_PATH

                self._log(f"Loading model into memory using {thread_count} threads via Langchain (GPU layers enabled if available)...")
                self.llm = LlamaCpp(
                    model_path=model_path,
                    n_ctx=2048, # Increased context to maximize Phi-3 potential
                    n_batch=256,
                    n_gpu_layers=-1,
                    n_threads=thread_count,
                    temperature=0.0,
                    top_p=0.1,
                    repeat_penalty=1.1,
                    stop=["<|end|>", "<|user|>"],
                    verbose=False,
                )
                self._log("Model ready.")
            else:
                # Fallback to direct llama_cpp
                try:
                    from llama_cpp import Llama
                except ModuleNotFoundError as exc:
                    raise RuntimeError(
                        "llama-cpp-python is not installed. Install it with a prebuilt wheel or add Visual C++ build tools before using the agent model."
                    ) from exc

                cpu_count = os.cpu_count() or 4
                thread_count = max(2, min(8, cpu_count - 1))

                self._log("Checking/downloading Phi-3.5 model...")
                models_manager.ensure_model_exists(progress_callback=self._download_progress)
                model_path = models_manager.get_model_path()
                self._log(f"Loading model into memory using {thread_count} threads (GPU layers enabled if available)...")
                self.llm = Llama(
                    model_path=model_path,
                    n_ctx=1024,
                    n_batch=256,
                    n_gpu_layers=-1,
                    n_threads=thread_count,
                    verbose=False,
                )
                self._log("Model ready.")

    def _download_progress(self, current, total):
        pct = (current / total) * 100
        rounded_pct = int(pct // 10) * 10
        if rounded_pct > self._last_download_pct and rounded_pct <= 100:
            self._last_download_pct = rounded_pct
            self._log(f"Downloading Model... {pct:.1f}%")

    def _normalize_key(self, value):
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    def _match_workflow_field(self, workflow_fields, aliases, default_label):
        normalized_aliases = [self._normalize_key(alias) for alias in aliases]
        for field in workflow_fields or []:
            normalized_field = self._normalize_key(field)
            if any(alias and alias in normalized_field for alias in normalized_aliases):
                return field
        return default_label

    def _extract_count(self, user_command):
        digit_match = re.search(r"\b(\d+)\b", user_command)
        if digit_match:
            return max(1, int(digit_match.group(1)))

        lowered = user_command.lower()
        for word, value in sorted(NUMBER_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                return value
        return 1

    def _build_fast_intent(self, user_command, workflow_fields=None):
        lowered = user_command.lower()
        count = self._extract_count(user_command)
        explicit_data = {}

        field_patterns = [
            (["ssn", "social security"], "SSN", r"\b\d{3}-\d{2}-\d{4}\b", 0),
            (["email"], "Email", r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", 0),
            (["phone", "mobile", "cell"], "Phone", r"\b(?:\+1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b", 0),
            (["zip", "zipcode", "postal code"], "Zip", r"\b\d{5}(?:-\d{4})?\b", 0),
            (["username", "user name"], "Username", r"\b(?:user\s*name|username)\b\s*(?:is|=|:)?\s*([^,;\s]+)", 1),
            (["password", "passcode"], "Password", r"\bpassword\b\s*(?:is|=|:)?\s*([^,;\s]+)", 1),
            (["first name"], "First Name", r"\bfirst\s+name\b\s*(?:is|=|:)?\s*([A-Za-z][A-Za-z' -]{0,40}?)(?=,|;|\band\b|$)", 1),
            (["last name", "surname"], "Last Name", r"\b(?:last\s+name|surname)\b\s*(?:is|=|:)?\s*([A-Za-z][A-Za-z' -]{0,40}?)(?=,|;|\band\b|$)", 1),
            (["dob", "date of birth", "birth date"], "DOB", r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b", 1),
            (["hire date", "date of hire", "employment date", "start date"], "Hire Date", r"\b(?:hire\s+date|date\s+of\s+hire|employment\s+date|start\s+date)\b\s*(?:is|=|:)?\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\b", 1),
            (["billing location", "billing", "subgroup", "subgroup id"], "Billing Location", r"\b(?:billing\s+location|billing|subgroup|subgroup\s+id)\b\s*(?:is|=|:)?\s*([A-Za-z0-9' -]{1,40}?)(?=,|;|\band\b|$)", 1),
            (["employee class", "class id", "employee type"], "Employee Class", r"\b(?:employee\s+class|class\s+id|employee\s+type|class)\b\s*(?:is|=|:)?\s*([A-Za-z0-9' -]{1,40}?)(?=,|;|\band\b|$)", 1),
        ]

        for aliases, default_label, pattern, group_index in field_patterns:
            if not any(alias in lowered for alias in aliases):
                continue
            match = re.search(pattern, user_command, re.IGNORECASE)
            if not match:
                continue
            value = match.group(group_index).strip()
            if value:
                field_name = self._match_workflow_field(workflow_fields, aliases, default_label)
                explicit_data[field_name] = value

        if "name is" in lowered and not any(self._normalize_key(key) in {"firstname", "lastname"} for key in explicit_data):
            match = re.search(r"\bname\b\s*(?:is|=|:)?\s*([A-Za-z][A-Za-z' -]{1,60})", user_command, re.IGNORECASE)
            if match:
                parts = [part for part in match.group(1).strip().split() if part]
                if len(parts) >= 2:
                    explicit_data.setdefault(self._match_workflow_field(workflow_fields, ["first name"], "First Name"), parts[0])
                    explicit_data.setdefault(self._match_workflow_field(workflow_fields, ["last name", "surname"], "Last Name"), parts[-1])

        extracted_data = [explicit_data] if explicit_data else []
        return ActionIntent(action="run_workflow", count=count, extracted_data=extracted_data)

    def _needs_llm(self, user_command):
        lowered = user_command.lower()
        complex_markers = [
            "instead of",
            "replace",
            "using these",
            "specific names",
            "specific values",
            "list:",
            "rows:",
            "csv",
            "json",
            "table",
            "for each",
            "each with",
        ]

        if any(marker in lowered for marker in complex_markers):
            return True

        if any(token in user_command for token in ["[", "]", "{", "}", "\n"]):
            return True

        multi_value_patterns = [
            r"\b\d{3}-\d{2}-\d{4}\b",
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        ]
        for pattern in multi_value_patterns:
            if len(re.findall(pattern, user_command, re.IGNORECASE)) > 1:
                return True

        return False

    def _get_workflow_fields(self, workflow_name):
        normalized_name = workflow_name.split('/')[-1].split('\\')[-1]
        if normalized_name in self._workflow_fields_cache:
            return list(self._workflow_fields_cache[normalized_name])

        workflow_fields = []
        try:
            workflow = web_core.load_workflow(normalized_name)
            for step in workflow.get("steps", []):
                field = step.get("label") or step.get("placeholder") or step.get("name") or step.get("id")
                if field and len(field) > 2 and field not in workflow_fields:
                    field_lower = field.lower()
                    if not any(token in field_lower for token in ["btn", "submit", "cancel"]):
                        workflow_fields.append(field)
        except FileNotFoundError:
            workflow_fields = []
        except Exception as exc:
            self._log(f"Error parsing workflow fields: {exc}")

        self._workflow_fields_cache[normalized_name] = list(workflow_fields)
        return workflow_fields

    def coordinator_agent(self, user_command, workflow_fields=None):
        """
        The Coordinator parsing an unstructured natural language command.
        Uses Phi-3.5 with strict instruction formatting to output JSON via LangChain if available.
        """
        self._log("Coordinator Agent: Parsing complex command with constrained JSON output...")

        fields_instruction = ""
        if workflow_fields:
            fields_instruction = f"""
Known workflow field names:
{workflow_fields}

Only use a field from this list when the user explicitly provides a value for it.
Prefer the closest matching field name from the list.
Do not invent new fields or generate fake values for unspecified fields.
"""

        if LANGCHAIN_AVAILABLE:
            parser = PydanticOutputParser(pydantic_object=ActionIntent)
            format_instructions = parser.get_format_instructions()

            template = f"""<|system|>
You are the Coordinator Agent. Your job is to parse the user's request into a reliable format.
Extract only the requested action, repetition count, and explicit values provided by the user.
If the user tells you to use a specific name or link INSTEAD of a previously recorded one, output the OLD name as the key and the NEW name as the value inside extracted_data.
{fields_instruction}
Rules:
- extracted_data must contain only explicit user constraints or replacement mappings from the user command.
- Never generate fake names, dates, SSNs, addresses, or any other filler values.
- If the user only asks for a count or a generic run, return extracted_data as [].
- Keep action as "run_workflow" unless the user clearly requests otherwise.

{{format_instructions}}

<|user|>
User Command: "{{user_command}}"
<|assistant|>
"""
            prompt_template = PromptTemplate(
                template=template,
                input_variables=["user_command"],
                partial_variables={"format_instructions": format_instructions}
            )

            with self._inference_lock:
                prompt_text = prompt_template.format(user_command=user_command)
                response = self.llm.invoke(prompt_text)

            output_text = response.strip()

            try:
                intent = parser.parse(output_text)
                self._log(
                    f"Coordinator parsed intent: Action='{intent.action}', Count={intent.count}, Records={len(intent.extracted_data)}"
                )
                return intent
            except Exception as exc:
                self._log(f"LangChain parser failed, attempting manual fallback extraction...")
                # Fallback to manual parsing
        else:
            # Fallback to original method if LangChain not available
            pass

        # Manual parsing fallback
        prompt = f"""<|system|>
You are the Coordinator Agent. Your job is to parse the user's request into a reliable JSON format.
Extract only the requested action, repetition count, and explicit values provided by the user.
If the user tells you to use a specific name or link INSTEAD of a previously recorded one, output the OLD name as the key and the NEW name as the value inside extracted_data.
{fields_instruction}
Rules:
- extracted_data must contain only explicit user constraints or replacement mappings from the user command.
- Never generate fake names, dates, SSNs, addresses, or any other filler values.
- If the user only asks for a count or a generic run, return extracted_data as [].
- Keep action as "run_workflow" unless the user clearly requests otherwise.
- Return strict JSON only.
DO NOT output anything other than raw valid JSON.

Format Required:
{{
  "action": "run_workflow",
  "count": 1,
  "extracted_data": [
      {{"First Name": "Alice", "SSN": "123-45-6789", "Old Item Name": "New Item Name"}}
  ]
}}
<|user|>
User Command: "{user_command}"
<|assistant|>
"""

        with self._inference_lock:
            response = self.llm(
                prompt,
                max_tokens=256,
                temperature=0.0,
                top_p=0.1,
                repeat_penalty=1.1,
                stop=["<|end|>", "<|user|>"],
                echo=False,
            )

        output_text = response["choices"][0]["text"].strip()

        try:
            if "```json" in output_text:
                output_text = output_text.split("```json")[1].split("```")[0].strip()
            elif "```" in output_text:
                output_text = output_text.split("```")[1].split("```")[0].strip()

            start_idx = output_text.find("{")
            if start_idx != -1:
                brace_count = 0
                for i, char in enumerate(output_text[start_idx:]):
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            output_text = output_text[start_idx:start_idx + i + 1]
                            break

            parsed = json.loads(output_text)

            if not parsed.get("action"):
                parsed["action"] = "run_workflow"
            if not parsed.get("count"):
                parsed["count"] = 1
            if not isinstance(parsed.get("extracted_data"), list):
                parsed["extracted_data"] = []

            intent = ActionIntent(**parsed)
            self._log(
                f"Coordinator parsed intent: Action='{intent.action}', Count={intent.count}, Records={len(intent.extracted_data)}"
            )
            return intent
        except Exception as exc:
            self._log(f"Coordinator parsed error: \nRaw Output: {output_text}\nError:{str(exc)}")
            return None

    def execute_workflow(self, user_command, target_url=None, custom_data=None, workflow_name="workflow.json"):
        workflow_fields = self._get_workflow_fields(workflow_name)
        fast_intent = self._build_fast_intent(user_command, workflow_fields)
        intent = None

        if self._needs_llm(user_command):
            self._log("Coordinator Agent: complex request detected, using LLM fallback.")
            try:
                self.load_model()
                intent = self.coordinator_agent(user_command, workflow_fields)
            except Exception as exc:
                self._log(f"Coordinator LLM unavailable, falling back to deterministic parser: {exc}")

            if not intent:
                self._log("Coordinator Agent: using deterministic fallback result.")
                intent = fast_intent
        else:
            self._log("Coordinator Agent: using deterministic parser for low-latency execution.")
            intent = fast_intent

        if not intent:
            self._log("Task cancelled - failed to parse intent.")
            return

        total = intent.count
        self._log(f"Orchestrating {total} loop(s) for task '{intent.action}'")

        data_to_loop = list(intent.extracted_data)

        if custom_data and isinstance(custom_data, list):
            data_to_loop.extend(custom_data)

        if len(data_to_loop) < total:
            data_to_loop.extend([{}] * (total - len(data_to_loop)))

        for i in range(total):
            import web_core
            if hasattr(web_core, 'stop_execution_event') and web_core.stop_execution_event.is_set():
                self._log("Execution stopped by user.")
                break

            record_data = data_to_loop[i]
            self._log(f"\n--- Starting automation run {i + 1} of {total} ---")
            self._log(f"Web Automation Agent: Executing Playwright task with data: {record_data}")

            success, msg, screenshot, effective_data = web_core.run_execution_mode(
                target_url,
                override_data=record_data,
                headless=False,
                workflow_name=workflow_name,
            )
            if not success:
                self._log(f"Web Automation Failed: {msg}")
                continue

            self._log("Web Automation completed. Firing Data & Verification Agent...")

            ssn_used = "unknown"
            data_used_for_verification = effective_data if isinstance(effective_data, dict) else record_data
            for key, value in data_used_for_verification.items():
                normalized_key = key.replace(" ", "").replace("_", "").replace("-", "").lower()
                if "ssn" in normalized_key or "socialsecurity" in normalized_key:
                    ssn_used = value
                    break

            import db_mock

            match_success, v_msg, row = db_mock.verify_customer_creation(ssn_used)
            report_path = verification.generate_report(screenshot, row, ssn_used, match_success, v_msg)

            if match_success:
                self._log(f"Verification Agent: Match SUCCESS! Report saved to {report_path}")
            else:
                self._log(f"Verification Agent: Match FAILED: {v_msg}. Report saved to {report_path}")

        self._log("\nAll tasks completed.")
