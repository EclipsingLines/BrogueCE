import atexit
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("BROGUE_EXE") or REPO / "bin" / ("brogue.exe" if os.name == "nt" else "brogue"))
PREFIX = b"@@BROGUE "
TIMEOUT = 30.0  # hang guard only; no assertion depends on time


def remove_bytecode_cache() -> None:
    # Discovery writes a .pyc into the repo; drop it so the run leaves no build artifact.
    shutil.rmtree(Path(__file__).parent / "__pycache__", ignore_errors=True)


atexit.register(remove_bytecode_cache)


class Game:
    def __init__(self, seed: int) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._closed = False
        self._returncode = None
        self.process = subprocess.Popen(
            [str(EXE), "--agent", "--seed", str(seed), "--data-dir", str(EXE.parent)],
            cwd=self._tmp.name,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.messages: queue.Queue = queue.Queue()
        self.reader = threading.Thread(target=self._read_lines, daemon=True)
        self.reader.start()

    def _read_lines(self) -> None:
        buffer = b""
        while True:
            chunk = self.process.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.startswith(PREFIX):
                    self.messages.put(json.loads(line[len(PREFIX):].decode("utf-8")))

    def read(self) -> dict:
        try:
            return self.messages.get(timeout=TIMEOUT)
        except queue.Empty:
            raise AssertionError("timed out waiting for a message from brogue")

    def read_obs(self) -> dict:
        while True:
            message = self.read()
            if message["type"] == "obs":
                return message
            if message["type"] == "exit":
                raise AssertionError("game exited before sending an observation")

    def send_key(self, key: int) -> None:
        self.send_raw(json.dumps({"key": key}).encode("utf-8") + b"\n")

    def send_raw(self, line: bytes) -> None:
        self.process.stdin.write(line)
        self.process.stdin.flush()

    def close(self) -> int:
        if self._closed:
            return self._returncode
        self._closed = True
        try:
            self.process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            self._returncode = self.process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self._returncode = self.process.wait()
        try:
            self.process.stdout.close()
        except OSError:
            pass
        self._tmp.cleanup()
        return self._returncode


class AgentHookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert EXE.exists(), (
            "build first: mingw32-make SYSTEM=WINDOWS GRAPHICS=NO TERMINAL=NO CC=gcc bin/brogue.exe"
        )

    def make_game(self, seed: int) -> Game:
        game = Game(seed)
        self.addCleanup(game.close)
        return game

    def test_first_observation_shape(self) -> None:
        game = self.make_game(42)
        obs = game.read_obs()

        self.assertEqual(obs["type"], "obs")
        self.assertEqual(len(obs["screen"]), 34)
        for row in obs["screen"]:
            self.assertEqual(len(row), 100)
        self.assertEqual(obs["depth"], 1)
        self.assertGreaterEqual(obs["turn"], 0)
        self.assertGreater(obs["hp"], 0)
        self.assertLessEqual(obs["hp"], obs["max_hp"])
        self.assertEqual(obs["nutrition"], 2150)
        self.assertEqual(obs["seed"], "42")
        players = sum(row.count("@") for row in obs["screen"][3:32])
        self.assertEqual(players, 1)

    def test_nutrition_drops_one_per_turn(self) -> None:
        game = self.make_game(42)
        first = game.read_obs()
        last = first
        for _ in range(20):
            game.send_key(ord("z"))
            last = game.read_obs()

        self.assertGreater(last["turn"], first["turn"])
        self.assertEqual(
            first["nutrition"] - last["nutrition"],
            last["turn"] - first["turn"],
        )

    def test_same_seed_same_screens(self) -> None:
        keys = [ord(c) for c in "zslhjkzzsz"]

        def screens(seed: int) -> list:
            game = self.make_game(seed)
            first = game.read_obs()
            result = [first["screen"]]
            for key in keys:
                game.send_key(key)
                result.append(game.read_obs()["screen"])
            return result

        screens_seven_a = screens(7)
        screens_seven_b = screens(7)
        screens_eight = screens(8)

        self.assertEqual(screens_seven_a, screens_seven_b)
        self.assertNotEqual(screens_seven_a[0], screens_eight[0])

    def test_bad_action_reports_error_and_keeps_waiting(self) -> None:
        game = self.make_game(42)
        first = game.read_obs()

        game.send_raw(b"not json\n")
        error = game.read()
        self.assertEqual(error["type"], "error")
        self.assertEqual(error["message"], "bad action")

        game.send_raw(b'{"nokey": 1}\n')
        error = game.read()
        self.assertEqual(error["type"], "error")
        self.assertEqual(error["message"], "bad action")

        game.send_key(ord("z"))
        obs = game.read_obs()
        self.assertEqual(obs["turn"], first["turn"] + 1)

    def test_eof_exits_cleanly(self) -> None:
        game = self.make_game(42)
        game.read_obs()
        self.assertEqual(game.close(), 0)


if __name__ == "__main__":
    unittest.main()
