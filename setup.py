#!/usr/bin/env python3
"""Cinema Stack — automated setup and configuration."""
import sys


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
