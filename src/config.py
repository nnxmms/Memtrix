#!/usr/bin/python3

import threading

# Config file location
CONFIG_PATH: str = "/home/memtrix/data/config.json"

# Lock for thread-safe config file read-modify-write operations
CONFIG_LOCK: threading.Lock = threading.Lock()
