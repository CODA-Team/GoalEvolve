from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.io import atomic_json


RETRYABLE_FAILURES = ("model is at capacity", "rate limit", "temporarily unavailable", "overloaded", "connection")
PROJECT_CREDENTIAL_ENV = Path(__file__).resolve().parents[2] / "config" / "credentials" / "goalevolve_codex.env"


@dataclass(frozen=True)
class CodexRuntimeConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "xhigh"
    retries: int = 3
    timeout_s: int = 3600
    seed_home: Path = Path("outputs/codex_home")
    credential_env: Path | None = PROJECT_CREDENTIAL_ENV
    # Persistent workers need short empirical continuity, but an unbounded
    # conversation makes every later edit pay for raw source/tool output from
    # old rounds.  Packets/EPD carry the durable evidence, so rotate threads
    # after two evolutionary rounds.
    max_session_rounds: int = 2


@dataclass(frozen=True)
class CodexTurn:
    ok: bool
    operation_id: str
    thread_id: str | None
    detail: str
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PersistentCodexRunner:
    """Execute auditable, resumable Codex turns for one logical worker identity."""

    def __init__(self, config: CodexRuntimeConfig) -> None:
        self.config = config

    def run(self, *, state_root: Path, identity: str, operation_id: str, cwd: Path, artifact_root: Path, prompt: str) -> CodexTurn:
        # Codex receives its isolated home through environment variables while
        # executing in a different directory; relative paths would otherwise
        # be resolved against that directory and accidentally look missing.
        state_root = state_root.resolve()
        cwd = cwd.resolve()
        artifact_root = artifact_root.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        events, stderr = artifact_root / "events.jsonl", artifact_root / "stderr.log"
        final_message, invocation, prompt_path = artifact_root / "last_message.md", artifact_root / "invocation.json", artifact_root / "prompt.md"
        session_state = state_root / "codex_sessions" / f"{identity}.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        home = self._ensure_home(state_root, identity)
        last_error = ""
        for attempt in range(1, self.config.retries + 1):
            previous = self._load_json(session_state)
            # A resumed Codex thread retains the model context that created
            # it.  Never silently continue a thread after a campaign changes
            # model or reasoning effort: doing so would make the invocation
            # say Terra while the persistent conversation was established by
            # a different worker.  The old ID remains in prior artifacts and
            # is retained in the session record for audit.
            requested_runtime = {"model": self.config.model, "reasoning_effort": self.config.reasoning_effort}
            previous_runtime = previous.get("runtime")
            operation_round = self._operation_round(operation_id)
            started_round = previous.get("started_round")
            age_compatible = (
                isinstance(started_round, int)
                and operation_round is not None
                and operation_round - started_round < self.config.max_session_rounds
            )
            compatible = previous_runtime == requested_runtime and age_compatible
            thread = str(previous.get("thread_id") or "").strip() if compatible else ""
            if compatible:
                session_reset_reason = ""
            elif previous_runtime != requested_runtime:
                session_reset_reason = "runtime_changed_or_legacy_session"
            else:
                session_reset_reason = "session_context_round_budget"
            command = self._command(cwd=cwd, final_message=final_message, prior_thread=thread)
            atomic_json(invocation, {"operation_id": operation_id, "attempt": attempt, "command": command, "cwd": str(cwd), "codex_home": str(home), "credential_source": str(self.config.credential_env or PROJECT_CREDENTIAL_ENV), **requested_runtime, "resumed_thread": thread or None, "session_reset_reason": session_reset_reason or None})
            print(f"[GoalEvolve][codex][{identity}] start attempt={attempt}/{self.config.retries} resume={str(bool(thread)).lower()}", flush=True)
            proc = self._invoke(command=command, prompt=prompt, cwd=cwd, home=home, events=events, stderr=stderr)
            thread_id, completed, usage = self._events(events)
            atomic_json(session_state, {"identity": identity, "thread_id": thread_id or thread or None, "last_operation_id": operation_id, "last_returncode": proc.returncode, "updated_at": int(time.time()), "usage": usage, "runtime": requested_runtime, "started_round": started_round if compatible else operation_round, "superseded_thread_id": str(previous.get("thread_id") or "") or None if session_reset_reason else None, "session_reset_reason": session_reset_reason or None})
            usage_path = artifact_root / f"token_usage_attempt_{attempt:02d}.json"
            atomic_json(usage_path, {"schema_version": "goalevolve.v2.codex_turn_usage.v1", "decision_role": "observer_only", "identity": identity, "operation_id": operation_id, "attempt": attempt, "thread_id": thread_id or thread or None, "returncode": proc.returncode, "completed": completed, "usage": usage})
            artifacts = {"codex_events": str(events), "codex_stderr": str(stderr), "codex_last_message": str(final_message), "codex_invocation": str(invocation), "codex_prompt": str(prompt_path), "codex_session": str(session_state), "codex_token_usage": str(usage_path)}
            if proc.returncode == 0 and completed:
                print(f"[GoalEvolve][codex][{identity}] completed thread={thread_id or 'unknown'}", flush=True)
                return CodexTurn(True, operation_id, thread_id, "codex_turn_completed", artifacts)
            last_error = self._tail(events) + "\n" + self._tail(stderr)
            retryable = any(token in last_error.lower() for token in RETRYABLE_FAILURES)
            print(f"[GoalEvolve][codex][{identity}] failed returncode={proc.returncode} retryable={str(retryable).lower()}", flush=True)
            if not retryable or attempt == self.config.retries:
                return CodexTurn(False, operation_id, thread_id, f"codex_failed:{last_error[-500:]}", artifacts)
            time.sleep(min(30, 3 * attempt))
        return CodexTurn(False, operation_id, None, f"codex_failed:{last_error[-500:]}", {})

    def _ensure_home(self, state_root: Path, identity: str) -> Path:
        home = state_root / "codex_homes" / identity
        # Sessions live under ``codex_sessions``; a worker home must not become
        # a second, stale source of credentials.  Refresh its generated auth
        # and provider configuration from the project dotenv before *every*
        # turn.  This means rotating the project key takes effect immediately
        # even for a long-running campaign with resumed Student threads.
        # Existing campaigns made by older runtimes are likewise migrated from
        # the project dotenv rather than reused as credential sources.
        home.mkdir(parents=True, mode=0o700, exist_ok=True)
        credentials = self._project_credentials()
        auth = home / "auth.json"
        config = home / "config.toml"
        auth.write_text(json.dumps({"OPENAI_API_KEY": credentials["key"]}, ensure_ascii=False) + "\n", encoding="utf-8")
        config.write_text(self._project_config(credentials), encoding="utf-8")
        for path in (auth, config):
            path.chmod(0o600)
        (home / ".goalevolve_project_credentials").write_text("project_env_bootstrap_v1\n", encoding="utf-8")
        return home

    def _project_credentials(self) -> dict[str, str]:
        """Load Codex access solely from the project-owned dotenv file.

        Worker homes are generated from this file and never copied from, or
        allowed to fall back to, the invoking user's ``~/.codex`` state.
        """
        path = self.config.credential_env or PROJECT_CREDENTIAL_ENV
        if not path.is_file():
            raise FileNotFoundError(f"project Codex credential file is missing: {path}")
        values: dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"invalid project credential entry in {path.name}")
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        required = {
            "GOALEVOLVE_OPENAI_API_KEY": "key",
            "GOALEVOLVE_PROVIDER_NAME": "provider",
            "GOALEVOLVE_BASE_URL": "base_url",
            "GOALEVOLVE_WIRE_API": "wire_api",
        }
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise RuntimeError(f"project Codex credential file lacks required entries: {', '.join(missing)}")
        credentials = {target: values[source] for source, target in required.items()}
        credentials["requires_auth"] = values.get("GOALEVOLVE_REQUIRES_AUTH", "true").lower()
        credentials["preferred_auth_method"] = values.get("GOALEVOLVE_PREFERRED_AUTH_METHOD", "apikey")
        credentials["disable_response_storage"] = values.get("GOALEVOLVE_DISABLE_RESPONSE_STORAGE", "true").lower()
        credentials["network_access"] = values.get("GOALEVOLVE_NETWORK_ACCESS", "true").lower()
        credentials["goals"] = values.get("GOALEVOLVE_GOALS", "false").lower()
        credentials["wsl_ack"] = values.get("GOALEVOLVE_WSL_ACK", "true").lower()
        return credentials

    def _project_config(self, credentials: dict[str, str]) -> str:
        quote = json.dumps
        boolean = lambda value: "true" if value == "true" else "false"
        return "\n".join(
            [
                f"model_provider = {quote(credentials['provider'])}",
                f"preferred_auth_method = {quote(credentials['preferred_auth_method'])}",
                f"model = {quote(self.config.model)}",
                f"review_model = {quote(self.config.model)}",
                f"model_reasoning_effort = {quote(self.config.reasoning_effort)}",
                f"disable_response_storage = {boolean(credentials['disable_response_storage'])}",
                f"network_access = {boolean(credentials['network_access'])}",
                f"windows_wsl_setup_acknowledged = {boolean(credentials['wsl_ack'])}",
                "",
                f"[model_providers.{credentials['provider']}]",
                f"name = {quote(credentials['provider'])}",
                f"base_url = {quote(credentials['base_url'])}",
                f"wire_api = {quote(credentials['wire_api'])}",
                f"requires_openai_auth = {boolean(credentials['requires_auth'])}",
                "",
                "[features]",
                f"goals = {boolean(credentials['goals'])}",
                # GoalEvolve workers are API-key-only and already receive all
                # project capabilities through their packets.  Remote plugin
                # discovery is unrelated to a source-edit/review turn and can
                # hang at startup when the plugin endpoint is unavailable.
                "plugins = false",
                "",
            ]
        )

    def _command(self, *, cwd: Path, final_message: Path, prior_thread: str) -> list[str]:
        common = ["--json", "-o", str(final_message), "--disable", "memories", "--disable", "plugins", "--model", self.config.model, "-c", f'model_reasoning_effort="{self.config.reasoning_effort}"', "--skip-git-repo-check"]
        # GoalEvolve workers run in the experiment's isolated, project-owned
        # Codex HOME and source snapshot.  They must not stop an unattended
        # evolutionary round to request interactive approval.  Keep new and
        # resumed threads identical here: ``--sandbox danger-full-access`` is
        # not equivalent because it can still retain the approval policy.
        # This explicit CLI switch disables both approvals and the nested
        # Codex sandbox; the outer experiment environment is responsible for
        # the isolation boundary.
        unattended = ["--dangerously-bypass-approvals-and-sandbox"]
        if prior_thread:
            return ["codex", "exec", "resume", *common, *unattended, prior_thread, "-"]
        return ["codex", "exec", *common, *unattended, "-C", str(cwd), "-"]

    @staticmethod
    def _load_json(path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _operation_round(operation_id: str) -> int | None:
        match = re.match(r"r(\d{3})_", operation_id)
        return int(match.group(1)) if match else None

    @staticmethod
    def _tail(path: Path, limit: int = 3000) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")[-limit:]
        except OSError:
            return ""

    @staticmethod
    def _events(path: Path) -> tuple[str | None, bool, dict[str, int]]:
        thread_id: str | None = None
        completed = False
        usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "thread.started":
                    thread_id = str(event.get("thread_id") or "") or thread_id
                if event.get("type") == "turn.completed":
                    completed = True
                    for key in usage:
                        usage[key] += int((event.get("usage") or {}).get(key, 0) or 0)
        return thread_id, completed, usage

    def _environment(self, *, home: Path, cwd: Path) -> dict[str, str]:
        credentials = self._project_credentials()
        env = dict(os.environ)
        # Do not inherit a shell or user-home credential. Both Codex's explicit
        # home and POSIX home point to the per-worker project-owned directory.
        env["CODEX_HOME"] = str(home)
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / "xdg_config")
        env["OPENAI_API_KEY"] = credentials["key"]
        for key in list(env):
            if key.startswith("GOALEVOLVE_"):
                env.pop(key)
        # Source snapshots intentionally omit .git. Prevent an agent's git
        # command from walking upward into an unrelated user repository.
        env["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
        return env

    def _invoke(self, *, command: list[str], prompt: str, cwd: Path, home: Path, events: Path, stderr: Path) -> subprocess.CompletedProcess[str]:
        env = self._environment(home=home, cwd=cwd)
        with events.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    # ``codex`` is a Node launcher for a native child.  A
                    # single-process kill leaves that child holding the pipe
                    # open after a timeout, which can wedge the selector and
                    # strand an evolution round.  Give every turn its own
                    # process group so the timeout is a real hard boundary.
                    start_new_session=True,
                )
                assert process.stdin is not None and process.stdout is not None and process.stderr is not None
                process.stdin.write(prompt)
                process.stdin.close()
                watched = selectors.DefaultSelector()
                watched.register(process.stdout, selectors.EVENT_READ, "stdout")
                watched.register(process.stderr, selectors.EVENT_READ, "stderr")
                deadline = time.monotonic() + self.config.timeout_s
                while watched.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._terminate_process_group(process)
                        return subprocess.CompletedProcess(command, 124, "", "timeout")
                    for key, _ in watched.select(timeout=min(1.0, remaining)):
                        line = key.fileobj.readline()
                        if not line:
                            watched.unregister(key.fileobj)
                            continue
                        if key.data == "stdout":
                            out.write(line)
                            out.flush()
                            self._print_progress(line)
                        else:
                            err.write(line)
                            err.flush()
                return subprocess.CompletedProcess(command, process.wait(), "", "")
            except subprocess.TimeoutExpired as exc:
                return subprocess.CompletedProcess(command, 124, str(exc.stdout or ""), str(exc.stderr or "timeout"))

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        """Terminate a timed-out Codex launcher and every descendant."""
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
    def _print_progress(line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        event_type = str(event.get("type") or "")
        if event_type in {"thread.started", "turn.started", "turn.completed"}:
            print(f"[GoalEvolve][codex] event={event_type}", flush=True)
        elif event_type == "item.started":
            item_type = str((event.get("item") or {}).get("type") or "")
            if item_type in {"command_execution", "agent_message"}:
                print(f"[GoalEvolve][codex] activity={item_type}", flush=True)
