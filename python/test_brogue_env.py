import atexit
import shutil
import unittest
from pathlib import Path

from brogue_env import (
    FAINT_THRESHOLD,
    HUNGER_THRESHOLD,
    MAP_COLS,
    MAP_ROWS,
    SCREEN_COLS,
    SCREEN_ROWS,
    STOMACH_SIZE,
    WEAK_THRESHOLD,
    BrogueEnv,
    Observation,
    find_executable,
    hunger_state,
)

HERE = Path(__file__).parent


def remove_bytecode_cache() -> None:
    # Discovery writes .pyc files into the repo; drop them so the run leaves no artifact.
    shutil.rmtree(HERE / "__pycache__", ignore_errors=True)
    shutil.rmtree(HERE / "brogue_env" / "__pycache__", ignore_errors=True)


atexit.register(remove_bytecode_cache)


class HungerStateTest(unittest.TestCase):
    def test_thresholds(self) -> None:
        cases = [
            (STOMACH_SIZE, "normal"),
            (HUNGER_THRESHOLD + 1, "normal"),
            (HUNGER_THRESHOLD, "hungry"),
            (WEAK_THRESHOLD + 1, "hungry"),
            (WEAK_THRESHOLD, "weak"),
            (FAINT_THRESHOLD + 1, "weak"),
            (FAINT_THRESHOLD, "faint"),
            (1, "faint"),
            (0, "starving"),
        ]
        for nutrition, expected in cases:
            with self.subTest(nutrition=nutrition):
                self.assertEqual(hunger_state(nutrition), expected)


class ObservationTest(unittest.TestCase):
    @staticmethod
    def make_message(screen: list[str] | None = None) -> dict:
        if screen is None:
            screen = [" " * SCREEN_COLS for _ in range(SCREEN_ROWS)]
        return {
            "type": "obs",
            "turn": 3,
            "depth": 2,
            "hp": 12,
            "max_hp": 15,
            "nutrition": 900,
            "gold": 7,
            "seed": "42",
            "text_input": False,
            "screen": screen,
        }

    def test_screen_layout(self) -> None:
        screen = [" " * SCREEN_COLS for _ in range(SCREEN_ROWS)]
        screen[5] = " " * 30 + "@" + " " * (SCREEN_COLS - 31)
        obs = Observation.from_message(self.make_message(screen))

        self.assertEqual(len(obs.map_rows), MAP_ROWS)
        for row in obs.map_rows:
            self.assertEqual(len(row), MAP_COLS)
        self.assertEqual(obs.messages, ("", "", ""))
        self.assertEqual(obs.player_position(), (9, 2))
        self.assertEqual(obs.seed, 42)

    def test_error_message_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Observation.from_message({"type": "error"})

    def test_wrong_screen_shape_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Observation.from_message(self.make_message([" " * SCREEN_COLS] * (SCREEN_ROWS - 1)))


class BrogueEnvTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.exe = find_executable()

    def make_env(self, seed: int) -> BrogueEnv:
        env = BrogueEnv(seed, exe=self.exe)
        self.addCleanup(env.close)
        return env

    def test_reset_returns_first_observation(self) -> None:
        env = self.make_env(42)
        obs = env.reset()

        self.assertEqual(obs.depth, 1)
        self.assertEqual(obs.nutrition, STOMACH_SIZE)
        self.assertEqual(obs.hunger, "normal")
        self.assertEqual(obs.seed, 42)
        self.assertIsNotNone(obs.player_position())

    def test_same_seed_same_trajectory(self) -> None:
        actions = [
            "rest", "search", "move_e", "move_s", "move_w", "move_n", "rest", "rest",
            "search", "rest", "move_ne", "move_sw", "rest", "rest", "rest",
        ]

        def trajectory(seed: int) -> list[Observation]:
            env = self.make_env(seed)
            result = [env.reset()]
            for action in actions:
                obs, done, _info = env.step(action)
                self.assertFalse(done)
                result.append(obs)
            return result

        self.assertEqual(trajectory(7), trajectory(7))

    def test_different_seeds_differ(self) -> None:
        first = self.make_env(7).reset()
        second = self.make_env(8).reset()

        self.assertNotEqual(first.map_rows, second.map_rows)

    def test_nutrition_tracks_turns(self) -> None:
        env = self.make_env(42)
        first = env.reset()
        last = first
        for _ in range(30):
            last, done, _info = env.step("rest")
            self.assertFalse(done)

        self.assertGreater(last.turn, first.turn)
        self.assertEqual(first.nutrition - last.nutrition, last.turn - first.turn)

    def test_reset_restarts_the_same_game(self) -> None:
        env = self.make_env(42)
        env.reset()
        for _ in range(3):
            obs, done, _info = env.step("rest")
            self.assertFalse(done)

        restarted = env.reset()
        fresh = self.make_env(42).reset()

        self.assertEqual(restarted, fresh)

    def test_quit_ends_the_game(self) -> None:
        env = self.make_env(42)
        env.reset()
        events: list[dict] = []

        _obs, done, info = env.step("quit")
        events.extend(info["events"])
        self.assertFalse(done)

        _obs, done, info = env.step("yes")
        events.extend(info["events"])
        for _ in range(100):
            if done:
                break
            _obs, done, info = env.step("acknowledge")
            events.extend(info["events"])

        self.assertTrue(done)
        self.assertTrue(any(event["name"] == "quit" for event in events))
        self.assertEqual(info["exit_code"], 0)
        self.assertTrue(env.recording_paths)
        with self.assertRaises(RuntimeError):
            env.step("rest")

    def test_unknown_action_raises_valueerror(self) -> None:
        env = self.make_env(42)
        env.reset()

        with self.assertRaisesRegex(ValueError, "move_n"):
            env.step("fly")

        _obs, done, _info = env.step("rest")
        self.assertFalse(done)

    def test_step_before_reset_raises(self) -> None:
        env = self.make_env(42)

        with self.assertRaises(RuntimeError):
            env.step("rest")

    def test_close_is_idempotent(self) -> None:
        env = self.make_env(42)
        env.reset()
        proc = env._proc
        workdir = env._workdir

        env.close()
        env.close()

        self.assertIsNotNone(proc)
        self.assertIsNotNone(proc.poll())
        self.assertIsNotNone(workdir)
        self.assertFalse(workdir.exists())

    def test_bad_seed_rejected(self) -> None:
        for seed in (0, -1, "42", True):
            with self.subTest(seed=seed):
                with self.assertRaises(ValueError):
                    BrogueEnv(seed, exe=self.exe)


if __name__ == "__main__":
    unittest.main()
