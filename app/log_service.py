import os
from datetime import datetime
from typing import Any, Callable, Dict, List


class LogService:
    def __init__(
        self,
        log_file: str,
        scanner_log_file: str,
        get_logs_queue: Callable[[], List[Dict[str, Any]]],
    ) -> None:
        self.log_file = log_file
        self.scanner_log_file = scanner_log_file
        self._get_logs_queue = get_logs_queue

    def capture_log(self, message: str, log_type: str = "SYSTEM") -> None:
        self._get_logs_queue().append({"msg": message, "type": log_type})
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"[{ts}] [{log_type}] {message}"
        print(formatted)
        try:
            with open(self.log_file, "a", encoding="utf-8") as file_obj:
                file_obj.write(formatted + "\n")
        except Exception:
            pass

    def clear_run_log(self) -> None:
        """Truncate recordandplay.logs and scanner logs so each run starts fresh."""
        try:
            self._get_logs_queue().clear()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_file, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    f"[{ts}] [SYSTEM] ══════════════════════ RUN STARTED ══════════════════════\n"
                )

            if os.path.exists(self.scanner_log_file):
                with open(self.scanner_log_file, "w", encoding="utf-8") as file_obj:
                    file_obj.write(f"=== Scanner Initialization [{ts}] ===\n\n")
        except Exception:
            pass
