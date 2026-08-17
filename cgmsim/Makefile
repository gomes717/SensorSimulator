CC      = gcc
CFLAGS  = -Wall -Wextra -std=c99 -I./inc
LDFLAGS = -lm

# ── Sources ────────────────────────────────────────────────────────────────
SRC = src/cgmsim_cambridge.c   \
      src/cgmsim_uva_padova.c  \
      src/cgmsim_royparker.c   \
      src/cgmsim_deichmann.c   \
      src/cgmsim_sensors.c

# ── Tests ──────────────────────────────────────────────────────────────────
TEST_SRC = tests/test_main.c       \
           tests/test_cambridge.c  \
           tests/test_uva_padova.c \
           tests/test_royparker.c  \
           tests/test_deichmann.c  \
           tests/test_sensors.c

TARGET = test_runner

# ── Main application ─────────────────────────────────────────────────────
APP_SRC = $(SRC) src/cgmsim_json.c src/cgmsim_params.c src/main.c
APP     = cgmsim

.PHONY: all test clean run

all: $(TARGET) $(APP)

$(TARGET): $(SRC) $(TEST_SRC)
	$(CC) $(CFLAGS) -o $@ $(SRC) $(TEST_SRC) $(LDFLAGS)

test: $(TARGET)
	./$(TARGET)

# Windows (MinGW / MSYS2) — use test_runner.exe
test-win: $(TARGET).exe
	$(TARGET).exe

$(TARGET).exe: $(SRC) $(TEST_SRC)
	$(CC) $(CFLAGS) -o $@ $(SRC) $(TEST_SRC) $(LDFLAGS)

$(APP): $(APP_SRC)
	$(CC) $(CFLAGS) -o $@ $(APP_SRC) $(LDFLAGS)

$(APP).exe: $(APP_SRC)
	$(CC) $(CFLAGS) -o $@ $(APP_SRC) $(LDFLAGS)

# Run a scenario, e.g.: make run SCENARIO=scenarios/royparker_day.json
run: $(APP)
	./$(APP) $(SCENARIO)

clean:
	rm -f $(TARGET) $(TARGET).exe $(APP) $(APP).exe
