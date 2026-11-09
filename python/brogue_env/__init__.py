"""A standard-library client for the Brogue CE agent hook.

``BrogueEnv`` starts ``brogue --agent`` in a private working directory and
exchanges JSON observations and actions with it over stdin and stdout.  The
observations expose the screen layout (messages, map and sidebar) and Brogue's
hunger thresholds so callers can reason about the game state without parsing
terminal output.

The protocol is one JSON object per ``@@BROGUE ``-prefixed line: observations
when the game waits for input, events when a game ends, and a single exit
message just before the process terminates.
"""

from .env import (
    ACTIONS,
    FAINT_THRESHOLD,
    HUNGER_THRESHOLD,
    MAP_COLS,
    MAP_LEFT,
    MAP_ROWS,
    MESSAGE_LINES,
    SCREEN_COLS,
    SCREEN_ROWS,
    SIDEBAR_COLS,
    STOMACH_SIZE,
    WEAK_THRESHOLD,
    BrogueEnv,
    BrogueError,
    BrogueTimeout,
    Observation,
    find_executable,
    hunger_state,
)

__all__ = [
    "ACTIONS",
    "BrogueEnv",
    "BrogueError",
    "BrogueTimeout",
    "FAINT_THRESHOLD",
    "HUNGER_THRESHOLD",
    "MAP_COLS",
    "MAP_LEFT",
    "MAP_ROWS",
    "MESSAGE_LINES",
    "Observation",
    "SCREEN_COLS",
    "SCREEN_ROWS",
    "SIDEBAR_COLS",
    "STOMACH_SIZE",
    "WEAK_THRESHOLD",
    "find_executable",
    "hunger_state",
]
