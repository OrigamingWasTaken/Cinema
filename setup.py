#!/usr/bin/env python3
"""Cinema Stack — automated setup and configuration."""
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REQUIRED_KEYS = ["PUID", "PGID", "TZ", "DATA_PATH", "CONFIG_PATH", "QB_PASSWORD"]


class EnvConfig:
    def __init__(self, env_path: str = ".env"):
        values = {}
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()

        missing = [k for k in REQUIRED_KEYS if k not in values]
        if missing:
            print(f"Missing required keys in {env_path}: {', '.join(missing)}")
            sys.exit(1)

        self.puid = values["PUID"]
        self.pgid = values["PGID"]
        self.tz = values["TZ"]
        self.data_path = values["DATA_PATH"]
        self.config_path = values["CONFIG_PATH"]
        self.qb_password = values["QB_PASSWORD"]
        self.all = values


DATA_DIRS = ["torrents/movies", "media/movies"]
CONFIG_SERVICES = ["qbittorrent", "radarr", "prowlarr", "flaresolverr"]


class DirectorySetup:
    def __init__(self, data_path: str, config_path: str):
        self.data_path = data_path
        self.config_path = config_path

    def create(self):
        for d in DATA_DIRS:
            os.makedirs(os.path.join(self.data_path, d), exist_ok=True)
        for svc in CONFIG_SERVICES:
            os.makedirs(os.path.join(self.config_path, svc), exist_ok=True)


class StackLauncher:
    HEALTH_ENDPOINTS = {
        "qbittorrent": "http://localhost:8080",
        "radarr": "http://localhost:7878/api/v3/system/status",
        "prowlarr": "http://localhost:9696/api/v1/system/status",
        "flaresolverr": "http://localhost:8191",
    }

    def start(self):
        print("Starting Docker stack...")
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"docker compose up failed:\n{result.stderr}")
            sys.exit(1)
        print("Containers started.")

    def wait_for_healthy(self, timeout: int = 120):
        print(f"Waiting for services to be ready (timeout: {timeout}s)...")
        start = time.time()
        pending = set(self.HEALTH_ENDPOINTS.keys())

        while pending and (time.time() - start) < timeout:
            for svc in list(pending):
                try:
                    req = Request(self.HEALTH_ENDPOINTS[svc])
                    urlopen(req, timeout=3)
                    print(f"  {svc} is ready")
                    pending.discard(svc)
                except Exception:
                    pass
            if pending:
                time.sleep(3)

        if pending:
            print(f"Timed out waiting for: {', '.join(pending)}")
            sys.exit(1)
        print("All services are ready.")
