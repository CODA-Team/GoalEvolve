from __future__ import annotations

import shutil
import os
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionPolicy:
    timeout_s: int = 7200
    retries: int = 1
    min_free_gb: float = 2.0


@dataclass(frozen=True)
class ExecutionReport:
    ok: bool
    attempts: int
    returncode: int | None
    stdout_tail: str
    stderr_tail: str
    resource_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ResilientCommandRunner:
    """Resource preflight and bounded retry for the built-in build, flow, and checker steps."""

    def __init__(self, policy: ExecutionPolicy = ExecutionPolicy()) -> None:
        self.policy = policy

    def run(
        self,
        *,
        command: list[str],
        cwd: Path,
        output_log: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> ExecutionReport:
        free_gb = shutil.disk_usage(cwd).free / (1024**3)
        if free_gb < self.policy.min_free_gb:
            print(f"[GoalEvolve][executor] resource_blocked free_gb={free_gb:.2f} required_gb={self.policy.min_free_gb:.2f}", flush=True)
            report = ExecutionReport(False, 0, None, "", "", f"insufficient_disk:{free_gb:.2f}GB<{self.policy.min_free_gb:.2f}GB")
            self._write_log(output_log, report)
            return report
        last: subprocess.CompletedProcess[str] | None = None
        command_label = Path(command[0]).name if command else "<empty>"
        for attempt in range(1, self.policy.retries + 2):
            print(
                f"[GoalEvolve][executor] start attempt={attempt}/{self.policy.retries + 1} command={command_label}",
                flush=True,
            )
            started = time.monotonic()
            live_log = self._open_live_log(output_log)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                # OpenROAD can create descendants that retain the output
                # pipes.  A direct-child kill then leaves communicate() hung
                # and strands the round instead of returning a repairable
                # flow failure to its Student.
                start_new_session=True,
            )
            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []

            def drain(stream: object, chunks: list[str]) -> None:
                assert hasattr(stream, "readline")
                for line in iter(stream.readline, ""):
                    chunks.append(line)
                    if live_log is not None:
                        live_log.write(line)
                        live_log.flush()

            assert process.stdout is not None and process.stderr is not None
            stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_chunks), daemon=True)
            stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_chunks), daemon=True)
            stdout_thread.start()
            stderr_thread.start()
            while True:
                remaining = self.policy.timeout_s - (time.monotonic() - started)
                if remaining <= 0:
                    self._terminate_process_group(process)
                    process.wait()
                    stdout_thread.join()
                    stderr_thread.join()
                    process.stdout.close()
                    process.stderr.close()
                    if live_log is not None:
                        live_log.close()
                    stdout, stderr = "".join(stdout_chunks), "".join(stderr_chunks)
                    print(f"[GoalEvolve][executor] timeout attempt={attempt} command={command_label}", flush=True)
                    report = ExecutionReport(False, attempt, None, stdout[-4000:], stderr[-4000:], "timeout")
                    return report
                try:
                    process.wait(timeout=min(30.0, remaining))
                except subprocess.TimeoutExpired:
                    elapsed = int(time.monotonic() - started)
                    print(f"[GoalEvolve][executor] running attempt={attempt} command={command_label} elapsed_s={elapsed}", flush=True)
                    continue
                stdout_thread.join()
                stderr_thread.join()
                process.stdout.close()
                process.stderr.close()
                if live_log is not None:
                    live_log.close()
                stdout, stderr = "".join(stdout_chunks), "".join(stderr_chunks)
                last = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                break
            if last.returncode == 0:
                print(f"[GoalEvolve][executor] completed attempt={attempt} command={command_label}", flush=True)
                report = ExecutionReport(True, attempt, 0, last.stdout[-4000:], last.stderr[-4000:])
                # Official QoR parsing consumes the complete OpenROAD log.
                # Keeping only the diagnostic tail loses report_tns/report_power
                # when a valid flow prints many DRV lines afterwards, which
                # converts a controller I/O truncation into a false Student
                # engineering failure.
                if output_log is not None and not output_log.exists():
                    self._write_log(output_log, report, stdout=last.stdout, stderr=last.stderr)
                return report
            print(f"[GoalEvolve][executor] failed attempt={attempt} returncode={last.returncode} command={command_label}", flush=True)
        assert last is not None
        resource_error = (
            f"terminated_signal:{-last.returncode}"
            if last.returncode is not None and last.returncode < 0
            else "command_failed"
        )
        report = ExecutionReport(False, self.policy.retries + 1, last.returncode, last.stdout[-4000:], last.stderr[-4000:], resource_error)
        if output_log is not None and not output_log.exists():
            self._write_log(output_log, report, stdout=last.stdout, stderr=last.stderr)
        return report

    @staticmethod
    def _open_live_log(output_log: Path | None) -> object | None:
        if output_log is None:
            return None
        output_log.parent.mkdir(parents=True, exist_ok=True)
        return output_log.open("w", encoding="utf-8")

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        """Stop a timed-out command and every pipe-holding descendant."""
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def _write_log(
        output_log: Path | None,
        report: ExecutionReport,
        *,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        if output_log is None:
            return
        output_log.parent.mkdir(parents=True, exist_ok=True)
        captured_stdout = report.stdout_tail if stdout is None else stdout
        captured_stderr = report.stderr_tail if stderr is None else stderr
        output_log.write_text(
            captured_stdout + ("\n" if captured_stdout and captured_stderr else "") + captured_stderr,
            encoding="utf-8",
        )
