CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -pthread -std=gnu11
LDFLAGS ?= -pthread

BIN = hstscan
SRC = hstscan.c

.PHONY: all clean test

all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS)

test: $(BIN)
	./test_hstscan.sh

clean:
	rm -f $(BIN)
