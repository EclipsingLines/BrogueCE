#include "../../external/cJSON/cJSON.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif
#include "platform.h"
#include "GlobalsBase.h"

#define AGENT_PREFIX "@@BROGUE "
#define AGENT_LINE_MAX 512

static unsigned int screenCells[ROWS][COLS];
static boolean screenInitialised = false;

static void initScreen(void) {
    int x, y;

    if (screenInitialised) {
        return;
    }
    for (y = 0; y < ROWS; y++) {
        for (x = 0; x < COLS; x++) {
            screenCells[y][x] = 0x20;
        }
    }
    screenInitialised = true;
}

static int encodeUtf8(unsigned int cp, char *out) {
    if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
        cp = 0xFFFD;
    }
    if (cp < 0x80) {
        out[0] = (char) cp;
        return 1;
    }
    if (cp < 0x800) {
        out[0] = (char) (0xC0 | (cp >> 6));
        out[1] = (char) (0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char) (0xE0 | (cp >> 12));
        out[1] = (char) (0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char) (0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (char) (0xF0 | (cp >> 18));
    out[1] = (char) (0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char) (0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char) (0x80 | (cp & 0x3F));
    return 4;
}

static void emit(cJSON *msg) {
    char *json = cJSON_PrintUnformatted(msg);

    if (json) {
        printf("%s%s\n", AGENT_PREFIX, json);
        cJSON_free(json);
    }
    fflush(stdout);
    cJSON_Delete(msg);
}

static cJSON *screenArray(void) {
    cJSON *screen = cJSON_CreateArray();
    char row[COLS * 4 + 1];
    int x, y, len;

    for (y = 0; y < ROWS; y++) {
        len = 0;
        for (x = 0; x < COLS; x++) {
            len += encodeUtf8(screenCells[y][x], &row[len]);
        }
        row[len] = '\0';
        cJSON_AddItemToArray(screen, cJSON_CreateString(row));
    }
    return screen;
}

static void emitObservation(boolean textInput) {
    cJSON *msg = cJSON_CreateObject();
    char seed[32];

    snprintf(seed, sizeof seed, "%llu", (unsigned long long) rogue.seed);

    cJSON_AddStringToObject(msg, "type", "obs");
    cJSON_AddNumberToObject(msg, "turn", (double) rogue.playerTurnNumber);
    cJSON_AddNumberToObject(msg, "depth", (double) rogue.depthLevel);
    cJSON_AddNumberToObject(msg, "hp", (double) player.currentHP);
    cJSON_AddNumberToObject(msg, "max_hp", (double) player.info.maxHP);
    cJSON_AddNumberToObject(msg, "nutrition", (double) player.status[STATUS_NUTRITION]);
    cJSON_AddNumberToObject(msg, "gold", (double) rogue.gold);
    cJSON_AddStringToObject(msg, "seed", seed);
    cJSON_AddBoolToObject(msg, "text_input", textInput);
    cJSON_AddItemToObject(msg, "screen", screenArray());
    emit(msg);
}

static void agent_gameLoop(void) {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    initScreen();

    int rc = rogueMain();

    cJSON *msg = cJSON_CreateObject();
    cJSON_AddStringToObject(msg, "type", "exit");
    cJSON_AddNumberToObject(msg, "code", (double) rc);
    emit(msg);
    exit(rc);
}

static boolean agent_pauseForMilliseconds(short milliseconds, PauseBehavior behavior) {
    return false;
}

static void agent_nextKeyOrMouseEvent(rogueEvent *returnEvent, boolean textInput, boolean colorsDance) {
    char line[AGENT_LINE_MAX];

    emitObservation(textInput);

    while (true) {
        cJSON *msg, *key, *ctrl, *shift;
        size_t len;

        if (!fgets(line, sizeof line, stdin)) {
            exit(0);
        }
        len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
            line[--len] = '\0';
        }

        msg = cJSON_Parse(line);
        key = msg ? cJSON_GetObjectItem(msg, "key") : NULL;
        if (msg && cJSON_IsObject(msg) && cJSON_IsNumber(key)) {
            ctrl = cJSON_GetObjectItem(msg, "ctrl");
            shift = cJSON_GetObjectItem(msg, "shift");
            returnEvent->eventType = KEYSTROKE;
            returnEvent->param1 = (signed long) key->valuedouble;
            returnEvent->param2 = 0;
            returnEvent->controlKey = cJSON_IsBool(ctrl) && cJSON_IsTrue(ctrl);
            returnEvent->shiftKey = cJSON_IsBool(shift) && cJSON_IsTrue(shift);
            cJSON_Delete(msg);
            return;
        }

        if (msg) {
            cJSON_Delete(msg);
        }
        cJSON *error = cJSON_CreateObject();
        cJSON_AddStringToObject(error, "type", "error");
        cJSON_AddStringToObject(error, "message", "bad action");
        emit(error);
    }
}

static void agent_plotChar(enum displayGlyph inputChar, short x, short y,
                           short foreRed, short foreGreen, short foreBlue,
                           short backRed, short backGreen, short backBlue) {
    unsigned int cp;

    if (x < 0 || x >= COLS || y < 0 || y >= ROWS) {
        return;
    }

    cp = glyphToUnicode(inputChar);
    screenCells[y][x] = cp ? cp : 0x20;
}

static boolean agent_modifierHeld(int modifier) {
    return false;
}

static void agent_notifyEvent(short eventId, int data1, int data2, const char *str1, const char *str2) {
    cJSON *msg = cJSON_CreateObject();
    const char *name;

    switch (eventId) {
        case GAMEOVER_QUIT:
            name = "quit";
            break;
        case GAMEOVER_DEATH:
            name = "death";
            break;
        case GAMEOVER_VICTORY:
            name = "victory";
            break;
        case GAMEOVER_SUPERVICTORY:
            name = "supervictory";
            break;
        case GAMEOVER_RECORDING:
            name = "recording";
            break;
        default:
            name = "other";
            break;
    }

    cJSON_AddStringToObject(msg, "type", "event");
    cJSON_AddStringToObject(msg, "name", name);
    cJSON_AddNumberToObject(msg, "id", (double) eventId);
    cJSON_AddNumberToObject(msg, "score", (double) data1);
    cJSON_AddNumberToObject(msg, "data2", (double) data2);
    cJSON_AddStringToObject(msg, "str1", str1 ? str1 : "");
    cJSON_AddStringToObject(msg, "str2", str2 ? str2 : "");
    emit(msg);
}

struct brogueConsole agentConsole = {
    agent_gameLoop,
    agent_pauseForMilliseconds,
    agent_nextKeyOrMouseEvent,
    agent_plotChar,
    NULL,
    agent_modifierHeld,
    agent_notifyEvent,
    NULL,
    NULL
};
