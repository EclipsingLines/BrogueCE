"""Drive a Brogue CE game through the headless agent hook."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

PREFIX = b"@@BROGUE "
SCREEN_COLS, SCREEN_ROWS = 100, 34
MESSAGE_LINES = 3
MAP_LEFT, MAP_COLS, MAP_ROWS = 21, 79, 29          # map = rows 3..31, cols 21..99
SIDEBAR_COLS = 20
STOMACH_SIZE, HUNGER_THRESHOLD, WEAK_THRESHOLD, FAINT_THRESHOLD = 2150, 350, 150, 50

ACTIONS: dict[str, int] = {
    "move_n": ord("k"), "move_s": ord("j"), "move_w": ord("h"), "move_e": ord("l"),
    "move_nw": ord("y"), "move_ne": ord("u"), "move_sw": ord("b"), "move_se": ord("n"),
    "rest": ord("z"), "search": ord("s"), "descend": ord(">"), "ascend": ord("<"),
    "explore": ord("x"), "apply": ord("a"), "inventory": ord("i"),
    "acknowledge": ord(" "), "escape": 27, "quit": ord("Q"), "yes": ord("y"), "no": ord("n"),
}


class BrogueError(RuntimeError):
    """The hook reported an error, or the game exited unexpectedly."""


class BrogueTimeout(BrogueError):
    """No message arrived from the hook within the configured timeout."""


def find_executable() -> Path:
    """Locate the brogue binary, or raise FileNotFoundError with build instructions."""
    if os.environ.get("BROGUE_EXE"):
        path = Path(os.environ["BROGUE_EXE"])
    else:
        repo = Path(__file__).resolve().parents[2]
        path = repo / "bin" / ("brogue.exe" if os.name == "nt" else "brogue")
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"brogue executable not found at {path}; build it with: "
            "mingw32-make SYSTEM=WINDOWS GRAPHICS=NO TERMINAL=NO CC=gcc bin/brogue.exe"
        )
    return path


def hunger_state(nutrition: int) -> str:
    """Map a nutrition value to Brogue's hunger label."""
    if nutrition > HUNGER_THRESHOLD:
        return "normal"
    if nutrition > WEAK_THRESHOLD:
        return "hungry"
    if nutrition > FAINT_THRESHOLD:
        return "weak"
    if nutrition > 0:
        return "faint"
    return "starving"


@dataclass(frozen=True)
class Observation:
    turn: int
    depth: int
    hp: int
    max_hp: int
    nutrition: int
    gold: int
    seed: int                 # parsed from the protocol's decimal string
    text_input: bool
    screen: tuple[str, ...]   # 34 rows, 100 code points each

    @classmethod
    def from_message(cls, msg: dict) -> "Observation":
        if msg.get("type") != "obs":
            raise ValueError("message is not an observation")
        screen = msg.get("screen")
        if not isinstance(screen, (list, tuple)) or len(screen) != SCREEN_ROWS:
            raise ValueError(f"observation screen must have {SCREEN_ROWS} rows")
        if any(not isinstance(row, str) or len(row) != SCREEN_COLS for row in screen):
            raise ValueError(f"observation screen rows must be {SCREEN_COLS} characters")
        return cls(
            turn=int(msg["turn"]),
            depth=int(msg["depth"]),
            hp=int(msg["hp"]),
            max_hp=int(msg["max_hp"]),
            nutrition=int(msg["nutrition"]),
            gold=int(msg["gold"]),
            seed=int(msg["seed"]),
            text_input=bool(msg["text_input"]),
            screen=tuple(screen),
        )

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(row[MAP_LEFT:MAP_LEFT + MAP_COLS].rstrip() for row in self.screen[:MESSAGE_LINES])

    @property
    def map_rows(self) -> tuple[str, ...]:
        return tuple(
            row[MAP_LEFT:MAP_LEFT + MAP_COLS]
            for row in self.screen[MESSAGE_LINES:MESSAGE_LINES + MAP_ROWS]
        )

    @property
    def sidebar(self) -> tuple[str, ...]:
        return tuple(row[:SIDEBAR_COLS].rstrip() for row in self.screen)

    @property
    def hunger(self) -> str:
        return hunger_state(self.nutrition)

    def player_position(self) -> tuple[int, int] | None:
        positions = [
            (x, y)
            for y, row in enumerate(self.map_rows)
            for x, char in enumerate(row)
            if char == "@"
        ]
        return positions[0] if len(positions) == 1 else None


class BrogueEnv:
    def __init__(self, seed: int, exe: str | os.PathLike | None = None,
                 workdir: str | os.PathLike | None = None, timeout: float = 30.0) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 1:
            raise ValueError("seed must be an integer >= 1")
        self._seed = seed
        self._exe = Path(exe) if exe is not None else find_executable()
        self._owns_workdir = workdir is None
        self._workdir = None if workdir is None else Path(workdir)
        self._timeout = float(timeout)
        self._proc: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict | None] | None = None
        self._reader: threading.Thread | None = None
        self._last_obs: Observation | None = None
        self._started = False
        self._running = False
        self._finished = False

    def _read_lines(self, proc: subprocess.Popen[bytes], messages: queue.Queue[dict | None]) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith(PREFIX):
                    messages.put(json.loads(line[len(PREFIX):].decode("utf-8")))
        finally:
            messages.put(None)

    def _start(self) -> None:
        if self._owns_workdir:
            self._workdir = Path(tempfile.mkdtemp())
        assert self._workdir is not None
        try:
            proc = subprocess.Popen(
                [str(self._exe), "--agent", "--seed", str(self._seed), "--data-dir", str(self._exe.parent)],
                cwd=self._workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except Exception:
            if self._owns_workdir:
                shutil.rmtree(self._workdir, ignore_errors=True)
                self._workdir = None
            raise
        messages: queue.Queue[dict | None] = queue.Queue()
        reader = threading.Thread(target=self._read_lines, args=(proc, messages), daemon=True)
        reader.start()
        self._proc = proc
        self._messages = messages
        self._reader = reader
        self._last_obs = None
        self._started = True
        self._running = True
        self._finished = False

    def _read_message(self) -> dict:
        assert self._messages is not None
        try:
            message = self._messages.get(timeout=self._timeout)
        except queue.Empty:
            raise BrogueTimeout(f"no message from brogue within {self._timeout:g} seconds") from None
        if message is None:
            self._running = False
            self._finished = True
            raise BrogueError("brogue exited without an exit message")
        return message

    def _send_key(self, key: int) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps({"key": key}).encode("utf-8") + b"\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # the reader thread reports the missing exit message

    def reset(self) -> Observation:
        self.close()
        self._start()
        while True:
            message = self._read_message()
            message_type = message.get("type")
            if message_type == "obs":
                obs = Observation.from_message(message)
                self._last_obs = obs
                return obs
            if message_type == "exit":
                self._running = False
                self._finished = True
                raise BrogueError("brogue exited before sending an observation")
            if message_type == "error":
                raise BrogueError(str(message.get("message", "bad action")))

    def step(self, action: str | int) -> tuple[Observation, bool, dict]:
        if not self._running or self._proc is None:
            raise RuntimeError("no running brogue game; call reset() first")
        if isinstance(action, str):
            try:
                key = ACTIONS[action]
            except KeyError:
                names = ", ".join(ACTIONS)
                raise ValueError(f"unknown action {action!r}; valid actions: {names}") from None
        elif isinstance(action, int):
            key = int(action)
        else:
            raise TypeError("action must be a name from ACTIONS or an int key code")

        self._send_key(key)
        info: dict = {"events": []}
        while True:
            message = self._read_message()
            message_type = message.get("type")
            if message_type == "obs":
                obs = Observation.from_message(message)
                self._last_obs = obs
                return obs, False, info
            if message_type == "event":
                info["events"].append(message)
            elif message_type == "error":
                raise BrogueError(str(message.get("message", "bad action")))
            elif message_type == "exit":
                self._running = False
                self._finished = True
                info["exit_code"] = int(message["code"])
                if self._last_obs is None:
                    raise BrogueError("brogue exited before sending an observation")
                return self._last_obs, True, info

    def close(self) -> None:
        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
            try:
                proc.wait(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
        self._proc = None
        self._messages = None
        self._reader = None
        self._last_obs = None
        self._running = False
        self._finished = True
        if self._owns_workdir and self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

    @property
    def recording_paths(self) -> list[Path]:
        if not self._started or self._workdir is None or not self._workdir.exists():
            return []
        return sorted(self._workdir.glob("*.broguerec"))

    @property
    def done(self) -> bool:
        return self._finished

    def __enter__(self) -> "BrogueEnv":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
