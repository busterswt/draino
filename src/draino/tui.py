from __future__ import annotations

import traceback
from queue import Queue
from threading import Thread

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Log, Static

from .config import load_config
from .models import StatusEvent, StepState, TargetNode, TargetSummary
from .ops import ClusterOperations, DrainoError
from .workflow import MaintenanceWorkflow


class TargetTable(DataTable):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.summaries: list[TargetSummary] = []

    def on_mount(self) -> None:
        self.add_columns("K8s Node", "Nova Host", "Total", "Migratable", "Amphora", "Compute", "K8s")

    @property
    def current_target(self) -> TargetNode | None:
        if not self.summaries or self.cursor_row >= len(self.summaries):
            return None
        return self.summaries[self.cursor_row].target

    def set_summaries(self, summaries: list[TargetSummary]) -> None:
        selected_target = self.current_target
        self.summaries = summaries
        self.clear(columns=False)
        for summary in summaries:
            row_style = self._row_style(summary)
            self.add_row(
                self._cell(summary.target.k8s_node, row_style),
                self._cell(summary.target.nova_compute_host, row_style),
                self._cell(str(summary.total_instances), row_style),
                self._cell(str(summary.migratable_instances), row_style),
                self._cell(str(summary.amphora_instances), row_style),
                self._cell(summary.compute_service_status, row_style),
                self._cell(summary.k8s_scheduling_status, row_style),
            )
        if summaries:
            selected_index = 0
            if selected_target is not None:
                for idx, summary in enumerate(summaries):
                    if (
                        summary.target.k8s_node == selected_target.k8s_node
                        and summary.target.nova_compute_host == selected_target.nova_compute_host
                    ):
                        selected_index = idx
                        break
            self.move_cursor(row=selected_index, column=0)
        self.refresh()

    def _row_style(self, summary: TargetSummary) -> str:
        compute_status = summary.compute_service_status.lower()
        k8s_status = summary.k8s_scheduling_status.lower()
        if compute_status.endswith("/down"):
            return "bold white on red"
        if summary.compute_service_status.startswith("disabled") and summary.k8s_scheduling_status == "cordoned":
            return "bold black on yellow"
        if compute_status.startswith("disabled"):
            return "bold black on khaki3"
        if k8s_status == "cordoned":
            return "bold black on wheat1"
        if compute_status.startswith("enabled") and compute_status.endswith("/up") and k8s_status == "schedulable":
            return "white on dark_green"
        return ""

    def _cell(self, value: str, style: str) -> Text | str:
        if not style:
            return value
        return Text(value, style=style)


class DrainoApp(App):
    CSS = """
    Screen {
      layout: vertical;
    }

    #body {
      height: 1fr;
    }

    #targets {
      width: 65%;
      border: solid green;
    }

    #events {
      width: 35%;
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
        ("m", "run_target", "Maintain"),
        ("f", "failover_target", "Failover"),
        ("r", "refresh_targets", "Refresh"),
    ]

    running = reactive(False)
    refreshing = reactive(False)

    def __init__(self, config_path: str | None):
        super().__init__()
        self.config = load_config(config_path)
        self.ops = ClusterOperations(self.config)
        self.targets: list[TargetNode] = []
        self.target_summaries: list[TargetSummary] = []
        self.discovery_error: str | None = None
        self.event_queue: Queue[StatusEvent] = Queue()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield TargetTable(id="targets")
            yield Log(id="events", auto_scroll=True, highlight=True)
        yield Static("Select a target, press m to run maintenance or f to fail over amphora", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_targets_async()
        self.set_interval(0.5, self._drain_events)
        self.set_interval(self.config.refresh_interval_seconds, self._auto_refresh_targets)

    def action_run_target(self) -> None:
        if self.running or not self.targets:
            return
        target = self.query_one(TargetTable).current_target
        if target is None:
            return
        self.running = True
        self.query_one("#status", Static).update(f"Running maintenance on {target.display_name}")
        thread = Thread(target=self._run_workflow, args=(target,), daemon=True)
        thread.start()

    def action_failover_target(self) -> None:
        if self.running or not self.targets:
            return
        target = self.query_one(TargetTable).current_target
        if target is None:
            return
        self.running = True
        self.query_one("#status", Static).update(f"Running failover on {target.display_name}")
        thread = Thread(target=self._run_failover, args=(target,), daemon=True)
        thread.start()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "targets":
            return
        target = event.data_table.current_target
        if target is None:
            return
        self.query_one("#status", Static).update(
            f"Selected {target.display_name}. Press m to run maintenance or f to fail over amphora"
        )

    def action_refresh_targets(self) -> None:
        if self.running or self.refreshing:
            return
        self._refresh_targets_async()

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
            self.call_from_thread(self._refresh_targets_async)

    def _run_failover(self, target: TargetNode) -> None:
        workflow = MaintenanceWorkflow(self.ops, self.event_queue.put)
        try:
            workflow.failover(target)
            self.event_queue.put(
                StatusEvent(step="complete", state=StepState.SUCCESS, message=f"Completed failover for {target.display_name}")
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
            self.call_from_thread(self._refresh_targets_async)

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

    def _refresh_targets_async(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.query_one("#status", Static).update("Refreshing target inventory...")
        thread = Thread(target=self._refresh_targets_worker, daemon=True)
        thread.start()

    def _refresh_targets_worker(self) -> None:
        discovery_error: str | None = None
        summary_error: str | None = None
        targets: list[TargetNode] = []
        summaries: list[TargetSummary] = []
        try:
            targets = self.ops.discover_targets()
        except Exception as exc:
            discovery_error = str(exc)
        if targets:
            try:
                summaries = self.ops.build_target_summaries(targets)
            except Exception as exc:
                summary_error = str(exc)
                summaries = [TargetSummary(target=target) for target in targets]
        self.call_from_thread(self._apply_refresh_results, targets, summaries, discovery_error, summary_error)

    def _apply_refresh_results(
        self,
        targets: list[TargetNode],
        summaries: list[TargetSummary],
        discovery_error: str | None,
        summary_error: str | None,
    ) -> None:
        self.targets = targets
        self.target_summaries = summaries
        self.discovery_error = discovery_error
        self.refreshing = False
        target_table = self.query_one(TargetTable)
        target_table.set_summaries(self.target_summaries)
        if self.target_summaries:
            target_table.focus()
            target_table.refresh()
        log = self.query_one(Log)
        status = self.query_one("#status", Static)
        if self.discovery_error:
            log.write_line(f"[FAILED] discovery: {self.discovery_error}")
            status.update(f"Target discovery failed: {self.discovery_error}")
        elif summary_error:
            log.write_line(f"[FAILED] summary: {summary_error}")
            status.update(f"Inventory loaded, but summary collection failed: {summary_error}")
        elif self.target_summaries:
            status.update("Select a target row. Press m to run maintenance or f to fail over amphora")
        else:
            status.update("No targets discovered")

    def _auto_refresh_targets(self) -> None:
        if self.refreshing:
            return
        self._refresh_targets_async()


def run_tui(config_path: str | None) -> None:
    app = DrainoApp(config_path)
    app.run()
