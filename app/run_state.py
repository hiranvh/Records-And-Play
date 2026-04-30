from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass
class RunState:
    get_logs_queue: Callable[[], List[Dict[str, Any]]]
    get_latest_recorded_steps: Callable[[], List[Dict[str, Any]]]
    set_latest_recorded_steps: Callable[[List[Dict[str, Any]]], None]
    get_last_recorded_url: Callable[[], str]
    set_last_recorded_url: Callable[[str], None]
    get_agent_run_result: Callable[[], Dict[str, Any]]
    set_agent_run_result: Callable[[Dict[str, Any]], None]

    def clear_logs_queue(self) -> None:
        self.get_logs_queue().clear()

    def append_log_event(self, message: str, event_type: str) -> None:
        self.get_logs_queue().append({"msg": message, "type": event_type})

    def consume_logs(self) -> List[Dict[str, Any]]:
        out = list(self.get_logs_queue())
        self.get_logs_queue().clear()
        return out

    def reset_agent_run_result(self) -> None:
        self.set_agent_run_result({})
