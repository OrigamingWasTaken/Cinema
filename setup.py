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
from typing import Optional
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
    HEALTH_PORTS = {
        "qbittorrent": 8080,
        "radarr": 7878,
        "prowlarr": 9696,
        "flaresolverr": 8191,
    }

    def start(self):
        print("Starting Docker stack...")
        result = subprocess.run(
            ["sudo", "docker", "compose", "up", "-d"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"docker compose up failed:\n{result.stderr}")
            sys.exit(1)
        print("Containers started.")

    @staticmethod
    def _port_open(port: int) -> bool:
        import socket
        try:
            with socket.create_connection(("localhost", port), timeout=2):
                return True
        except OSError:
            return False

    def wait_for_healthy(self, timeout: int = 120):
        print(f"Waiting for services to be ready (timeout: {timeout}s)...")
        start = time.time()
        pending = set(self.HEALTH_PORTS.keys())

        while pending and (time.time() - start) < timeout:
            for svc in list(pending):
                if self._port_open(self.HEALTH_PORTS[svc]):
                    print(f"  {svc} is ready")
                    pending.discard(svc)
            if pending:
                time.sleep(3)

        if pending:
            print(f"Timed out waiting for: {', '.join(pending)}")
            sys.exit(1)
        print("All services are ready.")

    @staticmethod
    def wait_for_config_files(config_path: str, timeout: int = 60):
        files = [
            os.path.join(config_path, "radarr", "config.xml"),
            os.path.join(config_path, "prowlarr", "config.xml"),
        ]
        print("Waiting for services to generate config files...")
        start = time.time()
        while (time.time() - start) < timeout:
            if all(os.path.exists(f) for f in files):
                print("  Config files found.")
                return
            time.sleep(3)
        missing = [f for f in files if not os.path.exists(f)]
        print(f"Timed out waiting for config files: {missing}")
        sys.exit(1)

    @staticmethod
    def configure_auth(config_path: str):
        for svc in ["radarr", "prowlarr"]:
            config_xml = os.path.join(config_path, svc, "config.xml")
            tree = ET.parse(config_xml)
            root = tree.getroot()

            def set_or_create(tag: str, value: str):
                el = root.find(tag)
                if el is None:
                    el = ET.SubElement(root, tag)
                el.text = value

            set_or_create("AuthenticationMethod", "Forms")
            set_or_create("AuthenticationRequired", "DisabledForLocalAddresses")
            set_or_create("AuthenticationType", "forms")
            tree.write(config_xml, xml_declaration=True, encoding="utf-8")
            print(f"  {svc}: auth set to Forms (disabled for local addresses)")

        print("  Restarting Radarr and Prowlarr to apply auth config...")
        subprocess.run(["sudo", "docker", "restart", "radarr", "prowlarr"],
                       capture_output=True, text=True)


class QBittorrentConfigurator:
    BASE_URL = "http://localhost:8080"

    def __init__(self, new_password: str):
        self.new_password = new_password
        self.cookie_jar = CookieJar()

    @staticmethod
    def parse_temp_password(logs: str) -> str:
        match = re.search(r"A temporary password is provided for this session: (\S+)", logs)
        if not match:
            raise ValueError("Could not find temporary password in qBittorrent logs")
        return match.group(1)

    def get_temp_password(self) -> str:
        result = subprocess.run(
            ["sudo", "docker", "logs", "qbittorrent"],
            capture_output=True, text=True,
        )
        return self.parse_temp_password(result.stdout + result.stderr)

    def _request(self, path: str, data: Optional[dict] = None) -> bytes:
        url = f"{self.BASE_URL}/api/v2{path}"
        body = urlencode(data).encode() if data else None
        req = Request(url, data=body)
        self.cookie_jar.add_cookie_header(req)
        resp = urlopen(req, timeout=10)
        self.cookie_jar.extract_cookies(resp, req)
        return resp.read()

    def login(self, password: str):
        self._request("/auth/login", {"username": "admin", "password": password})

    def configure(self):
        temp_pw = self.get_temp_password()
        self.login(temp_pw)

        self._request("/app/setPreferences", {
            "json": json.dumps({
                "save_path": "/data/torrents/movies",
                "upnp": False,
                "web_ui_password": self.new_password,
            })
        })

        self._request("/torrents/createCategory", {
            "category": "movies",
            "savePath": "/data/torrents/movies",
        })

        print("  qBittorrent configured (password set, save path, category created)")


class RadarrConfigurator:
    BASE_URL = "http://localhost:7878"

    def __init__(self, config_path: str, qb_password: str):
        config_xml = os.path.join(config_path, "radarr", "config.xml")
        self.api_key = self.read_api_key(config_xml)
        self.qb_password = qb_password

    @staticmethod
    def read_api_key(config_xml: str) -> str:
        tree = ET.parse(config_xml)
        el = tree.find("ApiKey")
        if el is None or not el.text:
            raise ValueError(f"No ApiKey found in {config_xml}")
        return el.text

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        url = f"{self.BASE_URL}/api/v3{path}"
        data = json.dumps(body).encode() if body else None
        req = Request(url, data=data, method=method)
        req.add_header("X-Api-Key", self.api_key)
        req.add_header("Content-Type", "application/json")
        resp = urlopen(req, timeout=10)
        content = resp.read()
        return json.loads(content) if content else None

    def configure(self):
        self._request("POST", "/rootfolder", {"path": "/data/media/movies"})

        self._request("POST", "/downloadclient", {
            "name": "qBittorrent",
            "implementation": "QBittorrent",
            "protocol": "torrent",
            "configContract": "QBittorrentSettings",
            "fields": [
                {"name": "host", "value": "qbittorrent"},
                {"name": "port", "value": 8080},
                {"name": "username", "value": "admin"},
                {"name": "password", "value": self.qb_password},
                {"name": "movieCategory", "value": "movies"},
            ],
            "enable": True,
        })

        naming = self._request("GET", "/config/naming")
        naming["renameMovies"] = True
        naming["standardMovieFormat"] = "{Movie CleanTitle} ({Release Year}) - {Quality Full}"
        naming["movieFolderFormat"] = "{Movie CleanTitle} ({Release Year})"
        self._request("PUT", "/config/naming", naming)

        print("  Radarr configured (root folder, download client, naming)")


class ProwlarrConfigurator:
    BASE_URL = "http://localhost:9696"

    def __init__(self, config_path: str, radarr_api_key: str):
        config_xml = os.path.join(config_path, "prowlarr", "config.xml")
        self.api_key = self.read_api_key(config_xml)
        self.radarr_api_key = radarr_api_key

    @staticmethod
    def read_api_key(config_xml: str) -> str:
        tree = ET.parse(config_xml)
        el = tree.find("ApiKey")
        if el is None or not el.text:
            raise ValueError(f"No ApiKey found in {config_xml}")
        return el.text

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        url = f"{self.BASE_URL}/api/v1{path}"
        data = json.dumps(body).encode() if body else None
        req = Request(url, data=data, method=method)
        req.add_header("X-Api-Key", self.api_key)
        req.add_header("Content-Type", "application/json")
        resp = urlopen(req, timeout=10)
        content = resp.read()
        return json.loads(content) if content else None

    def configure(self):
        self._request("POST", "/tag", {"label": "flaresolverr"})
        tags = self._request("GET", "/tag")
        fs_tag_id = next(t["id"] for t in tags if t["label"] == "flaresolverr")

        self._request("POST", "/indexerproxy", {
            "name": "FlareSolverr",
            "implementation": "FlareSolverr",
            "configContract": "FlareSolverrSettings",
            "fields": [
                {"name": "host", "value": "http://flaresolverr:8191"},
                {"name": "requestTimeout", "value": 60},
            ],
            "tags": [fs_tag_id],
        })

        self._request("POST", "/applications", {
            "name": "Radarr",
            "implementation": "Radarr",
            "configContract": "RadarrSettings",
            "syncLevel": "fullSync",
            "fields": [
                {"name": "prowlarrUrl", "value": "http://prowlarr:9696"},
                {"name": "baseUrl", "value": "http://radarr:7878"},
                {"name": "apiKey", "value": self.radarr_api_key},
            ],
            "tags": [],
        })

        print("  Prowlarr configured (FlareSolverr proxy, Radarr application)")


def main():
    print("=" * 50)
    print("Cinema Stack Setup")
    print("=" * 50)

    print("\n[Phase 1] Loading configuration...")
    if not os.path.exists(".env"):
        if os.path.exists(".env.example"):
            import shutil
            print("No .env file found. Copying .env.example to .env")
            print("Please edit .env with your values and re-run this script.")
            shutil.copy(".env.example", ".env")
            sys.exit(0)
        else:
            print("No .env or .env.example found.")
            sys.exit(1)

    config = EnvConfig(".env")
    print(f"  Data path: {config.data_path}")
    print(f"  Config path: {config.config_path}")
    print(f"  PUID:PGID = {config.puid}:{config.pgid}")

    print("\n[Phase 2] Creating directories...")
    dirs = DirectorySetup(config.data_path, config.config_path)
    dirs.create()
    print("  Directories created.")

    print("\n[Phase 3] Launching Docker stack...")
    launcher = StackLauncher()
    launcher.start()
    launcher.wait_for_healthy()
    launcher.wait_for_config_files(config.config_path)

    print("\n[Phase 3b] Configuring authentication...")
    launcher.configure_auth(config.config_path)
    time.sleep(10)
    launcher.wait_for_healthy()

    print("\n[Phase 4] Configuring services...")

    print("  Configuring qBittorrent...")
    qb = QBittorrentConfigurator(config.qb_password)
    qb.configure()

    print("  Configuring Radarr...")
    radarr = RadarrConfigurator(config.config_path, config.qb_password)
    radarr.configure()

    print("  Configuring Prowlarr...")
    prowlarr = ProwlarrConfigurator(config.config_path, radarr.api_key)
    prowlarr.configure()

    print("\n" + "=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print("\nService URLs (replace localhost with your Synology IP):")
    print("  Radarr:       http://localhost:7878")
    print("  Prowlarr:     http://localhost:9696")
    print("  qBittorrent:  http://localhost:8080")
    print("  FlareSolverr: http://localhost:8191")
    print("\nqBittorrent credentials: admin / <your QB_PASSWORD from .env>")
    print("\nRemaining manual steps:")
    print("  1. Open Prowlarr and add torrent indexers (1337x, etc.)")
    print(f"  2. On Apple TV: open Infuse -> add SMB share ->")
    print(f"     point to {config.data_path}/media/movies")
    print("  3. On iOS: install LunaSea, add Radarr server")
    print("     (or use Radarr web UI in browser on any device)")
    print("  4. Optional: adjust quality profiles in Radarr")


if __name__ == "__main__":
    main()
