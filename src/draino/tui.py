from __future__ import annotations

import traceback
from queue import Queue
from threading import Thread

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Log, Static

from .config import load_config
from .models import StatusEvent, StepState, TargetNode
from .ops import ClusterOperations, DrainoError
from .workflow import MaintenanceWorkflow


class TargetList(Static):
    def __init__(self, targets: list[TargetNode], **kwargs):
        super().__init__(**kwargs)
        self.targets = targets
        self.index = 0

    def render(self) -> str:
        lines = ["Targets", ""]
        for idx, target in enumerate(self.targets):
            prefix = ">" if idx == self.index else " "
            notes = f" [{target.notes}]" if target.notes else ""
            lines.append(f"{prefix} {target.display_name}{notes}")
        return "\n".join(lines)

    @property
    def current(self) -> TargetNode:
        return self.targets[self.index]

    def move(self, delta: int) -> None:
        if not self.targets:
            return
        self.index = max(0, min(self.index + delta, len(self.targets) - 1))
        self.refresh()


class DrainoApp(App):
    CSS = """
    Screen {
      layout: vertical;
    }

    #body {
      height: 1fr;
    }

    #targets {
      width: 40%;
      border: solid green;
      padding: 1;
    }

    #events {
      width: 60%;
      border: solid cyan;
    }

    #status {
      height: 3;
      border: solid yellow;
      padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("enter", "run_target", "Run"),
    ]

    running = reactive(False)

    def __init__(self, config_path: str | None):
        super().__init__()
        self.config = load_config(config_path)
        self.ops = ClusterOperations(self.config)
        self.targets: list[TargetNode] = []
        self.discovery_error: str | None = None
        self.event_queue: Queue[StatusEvent] = Queue()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            fallback = self.targets or [
                TargetNode(k8s_node="n/a", nova_compute_host="n/a", display_name="No targets discovered")
            ]
            yield TargetList(fallback, id="targets")
            yield Log(id="events", auto_scroll=True, highlight=True)
        yield Static("Select a target and press Enter", id="status")
        yield Footer()

    def on_mount(self) -> None:
        try:
            self.targets = self.ops.discover_targets()
        except Exception as exc:
            self.discovery_error = str(exc)
        target_list = self.query_one(TargetList)
        target_list.targets = self.targets or [
            TargetNode(k8s_node="n/a", nova_compute_host="n/a", display_name="No targets discovered")
        ]
        target_list.index = 0
        target_list.refresh()
        if self.discovery_error:
            self.query_one(Log).write_line(f"[FAILED] discovery: {self.discovery_error}")
            self.query_one("#status", Static).update("Target discovery failed; check kubectl/openstack access")
        self.set_interval(0.5, self._drain_events)

    def action_cursor_up(self) -> None:
        if self.running:
            return
        self.query_one(TargetList).move(-1)

    def action_cursor_down(self) -> None:
        if self.running:
            return
        self.query_one(TargetList).move(1)

    def action_run_target(self) -> None:
        if self.running or not self.targets:
            return
        target = self.query_one(TargetList).current
        self.running = True
        self.query_one("#status", Static).update(f"Running maintenance on {target.display_name}")
        thread = Thread(target=self._run_workflow, args=(target,), daemon=True)
        thread.start()

    def _run_workflow(self, target: TargetNode) -> None:
        workflow = MaintenanceWorkflow(self.ops, self.event_queue.put)
        try:
            workflow.run(target)
            self.event_queue.put(
                StatusEvent(step="complete", state=StepState.SUCCESS, message=f"Completed {target.display_name}")
            )
        except DrainoError as exc:
            self.event_queue.put(StatusEvent(step="error", state=StepState.FAILED, message=str(exc)))
        except Exception:
            self.event_queue.put(
                StatusEvent(
                    step="error",
                    state=StepState.FAILED,
                    message=traceback.format_exc(),
                )
            )
        finally:
            self.running = False

    def _drain_events(self) -> None:
        log = self.query_one(Log)
        status = self.query_one("#status", Static)
        while not self.event_queue.empty():
            event = self.event_queue.get()
            suffix = ""
            if event.details:
                suffix = f" | {event.details}"
            log.write_line(f"[{event.state.value.upper()}] {event.step}: {event.message}{suffix}")
            status.update(event.message)


def run_tui(config_path: str | None) -> None:
    app = DrainoApp(config_path)
    app.run()
