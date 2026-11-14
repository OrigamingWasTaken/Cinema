#!/usr/bin/env python3
"""Cinema Stack v2 — automated setup and configuration (stdlib only)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.request import Request, urlopen


REQUIRED_KEYS = ["PUID", "PGID", "TZ", "DATA_PATH", "CONFIG_PATH", "TMDB_TOKEN"]


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

        missing = [k for k in REQUIRED_KEYS if not values.get(k)]
        if missing:
            print(f"Missing required keys in {env_path}: {', '.join(missing)}")
            sys.exit(1)

        self.puid = values["PUID"]
        self.pgid = values["PGID"]
        self.tz = values["TZ"]
        self.data_path = values["DATA_PATH"]
        self.config_path = values["CONFIG_PATH"]
        self.tmdb_token = values["TMDB_TOKEN"]
        self.v1_config_path = values.get("V1_CONFIG_PATH") or None
        self.all = values


DATA_DIRS = ["movies"]
CONFIG_SERVICES = ["prowlarr", "rqbit/db", "rqbit/cache", "cinema"]

DEFAULT_INDEXERS = ["The Pirate Bay", "YTS", "Knaben"]


def docker_bin() -> str:
    # Synology's non-interactive and sudo PATH lacks /usr/local/bin.
    return shutil.which("docker") or "/usr/local/bin/docker"


class DirectorySetup:
    def __init__(self, data_path: str, config_path: str):
        self.data_path = data_path
        self.config_path = config_path

    def create(self):
        # Synology Docker refuses to create missing bind-mount directories.
        for d in DATA_DIRS:
            os.makedirs(os.path.join(self.data_path, d), exist_ok=True)
        for svc in CONFIG_SERVICES:
            os.makedirs(os.path.join(self.config_path, svc), exist_ok=True)


def prowlarr_running() -> bool:
    """True when a container named exactly `prowlarr` is running (the v1 one uses that name)."""
    result = subprocess.run(["sudo", docker_bin(), "ps", "-q", "-f", "name=^prowlarr$"],
                            capture_output=True, text=True)
    return bool(result.stdout.strip())


def migrate_prowlarr_config(v1_config_path: Optional[str], config_path: str) -> bool:
    """Copy the v1 Prowlarr folder into the v2 config unless it already has a config.xml.

    config.xml is copied last, so a present config.xml means the copy completed.
    Refuses while a `prowlarr` container runs: v1 would keep writing its database
    mid-copy, and v2 needs the same container name and port.
    """
    if not v1_config_path:
        return False
    source = os.path.join(v1_config_path, "prowlarr")
    target = os.path.join(config_path, "prowlarr")
    source_xml = os.path.join(source, "config.xml")
    if not os.path.isfile(source_xml):
        return False
    if os.path.exists(os.path.join(target, "config.xml")):
        return False
    if prowlarr_running():
        print("A Prowlarr container is still running (the v1 stack). Stop v1 first;")
        print("its configs stay in place as a backup:")
        print("  cd <v1 dir> && sudo docker compose down")
        print("Then run this again: sudo python3 setup.py")
        sys.exit(1)
    try:
        shutil.copytree(source, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("config.xml"))
        shutil.copy2(source_xml, os.path.join(target, "config.xml"))
    except (shutil.Error, OSError) as e:
        print(f"Could not copy the v1 Prowlarr files ({e}).")
        print("They are not readable by this user; re-run as: sudo python3 setup.py")
        sys.exit(1)
    return True


class StackLauncher:
    HEALTH_PORTS = {"prowlarr": 9696, "cinema": 8888}

    def start(self):
        print("Starting Docker stack...")
        result = subprocess.run(
            ["sudo", docker_bin(), "compose", "up", "-d", "--build"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"docker compose up failed:\n{result.stderr}")
            sys.exit(1)
        print("Containers started.")

    @staticmethod
    def _port_open(port: int) -> bool:
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
            print(f"Timed out waiting for: {', '.join(sorted(pending))}")
            sys.exit(1)
        print("All services are ready.")

    @staticmethod
    def wait_for_config_files(config_path: str, timeout: int = 60):
        files = [os.path.join(config_path, "prowlarr", "config.xml")]
        print("Waiting for Prowlarr to generate its config file...")
        start = time.time()
        while (time.time() - start) < timeout:
            if all(os.path.exists(f) for f in files):
                print("  Config file found.")
                return
            time.sleep(3)
        missing = [f for f in files if not os.path.exists(f)]
        print(f"Timed out waiting for config files: {missing}")
        sys.exit(1)

    @staticmethod
    def configure_auth(config_path: str):
        config_xml = os.path.join(config_path, "prowlarr", "config.xml")
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
        print("  prowlarr: auth set to Forms (disabled for local addresses)")

        print("  Restarting Prowlarr to apply auth config...")
        result = subprocess.run(["sudo", docker_bin(), "restart", "prowlarr"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  WARNING: restarting Prowlarr failed:\n{result.stderr}")


def plan_prowlarr_changes(proxies: list, applications: list) -> tuple:
    """Return (add FlareSolverr proxy?, ids of stale Radarr applications to remove)."""
    add_proxy = not any(p.get("implementation") == "FlareSolverr" for p in proxies)
    remove_ids = [a["id"] for a in applications if a.get("implementation") == "Radarr"]
    return add_proxy, remove_ids


def plan_indexers(indexers: list) -> list:
    """Names of public indexers to add: only when Prowlarr has none at all."""
    return [] if indexers else list(DEFAULT_INDEXERS)


class ProwlarrConfigurator:
    BASE_URL = "http://localhost:9696"

    def __init__(self, config_path: str):
        config_xml = os.path.join(config_path, "prowlarr", "config.xml")
        self.api_key = self.read_api_key(config_xml)

    @staticmethod
    def read_api_key(config_xml: str) -> str:
        tree = ET.parse(config_xml)
        el = tree.find("ApiKey")
        if el is None or not el.text:
            raise ValueError(f"No ApiKey found in {config_xml}")
        return el.text

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        url = f"{self.BASE_URL}/api/v1{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = Request(url, data=data, method=method)
        req.add_header("X-Api-Key", self.api_key)
        req.add_header("Content-Type", "application/json")
        # Generous timeout: forceSave calls can wait on slow upstreams.
        resp = urlopen(req, timeout=60)
        content = resp.read()
        return json.loads(content) if content else None

    def _flaresolverr_tag_id(self) -> int:
        for tag in self._request("GET", "/tag"):
            if tag["label"] == "flaresolverr":
                return tag["id"]
        return self._request("POST", "/tag", {"label": "flaresolverr"})["id"]

    def wait_until_ready(self, timeout: int = 60, interval: float = 3):
        """Poll /system/status: docker-proxy accepts TCP before the app is up."""
        deadline = time.time() + timeout
        while True:
            try:
                self._request("GET", "/system/status")
                return
            except Exception as e:
                if time.time() >= deadline:
                    print(f"  WARNING: Prowlarr API not ready after {timeout}s: {e}")
                    return
                time.sleep(interval)

    def configure(self):
        self.wait_until_ready()
        proxies = self._request("GET", "/indexerProxy")
        applications = self._request("GET", "/applications")
        add_proxy, remove_ids = plan_prowlarr_changes(proxies, applications)

        if add_proxy:
            try:
                tag_id = self._flaresolverr_tag_id()
                # forceSave: validation solves a Cloudflare challenge via FlareSolverr,
                # which can time out on the NAS and return 400.
                self._request("POST", "/indexerProxy?forceSave=true", {
                    "name": "FlareSolverr",
                    "implementation": "FlareSolverr",
                    "configContract": "FlareSolverrSettings",
                    "fields": [
                        {"name": "host", "value": "http://flaresolverr:8191/"},
                        {"name": "requestTimeout", "value": 60},
                    ],
                    "tags": [tag_id],
                })
                print("  FlareSolverr proxy added")
            except Exception as e:
                print(f"  WARNING: could not add the FlareSolverr proxy: {e}")
        else:
            print("  FlareSolverr proxy already present")

        for app_id in remove_ids:
            try:
                self._request("DELETE", f"/applications/{app_id}")
                print(f"  Removed stale Radarr application {app_id}")
            except Exception as e:
                print(f"  WARNING: could not remove Radarr application {app_id}: {e}")

        wanted = plan_indexers(self._request("GET", "/indexer"))
        if wanted:
            self._add_indexers(wanted)

    def _add_indexers(self, wanted: list):
        schemas = self._request("GET", "/indexer/schema")
        for name in wanted:
            try:
                schema = next((s for s in schemas if s.get("name") == name), None)
                if schema is None:
                    print(f"  WARNING: {name} not in Prowlarr schema")
                    continue
                schema = dict(schema, enable=True, appProfileId=1, priority=25, tags=[])
                self._request("POST", "/indexer?forceSave=true", schema)
                print(f"  Indexer added: {name}")
            except Exception as e:
                print(f"  WARNING: could not add indexer {name}: {e}")


def dav_auth_warning(dav_user: Optional[str], dav_password: Optional[str]) -> Optional[str]:
    """The cinema app turns WebDAV auth on only when BOTH DAV_USER and DAV_PASSWORD are set."""
    if bool(dav_user) != bool(dav_password):
        return ("WARNING: only one of DAV_USER / DAV_PASSWORD is set, so WebDAV has no "
                "login. Set both to require one.")
    return None


def infuse_login_lines(dav_user: Optional[str], dav_password: Optional[str]) -> list:
    if dav_user and dav_password:
        return [f"       username {dav_user}, password = DAV_PASSWORD from .env"]
    lines = ["       leave username and password empty"]
    warning = dav_auth_warning(dav_user, dav_password)
    if warning:
        lines.append(f"       ({warning})")
    return lines


def detect_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return "<NAS-IP>"


def main():
    print("=" * 50)
    print("Cinema Stack Setup")
    print("=" * 50)

    print("\n[Phase 1] Loading configuration...")
    if not os.path.exists(".env"):
        if os.path.exists(".env.example"):
            print("No .env file found. Copying .env.example to .env")
            print("Please edit .env with your values (incl. TMDB_TOKEN) and re-run this script.")
            shutil.copy(".env.example", ".env")
            sys.exit(0)
        else:
            print("No .env or .env.example found.")
            sys.exit(1)

    config = EnvConfig(".env")
    print(f"  Data path: {config.data_path}")
    print(f"  Config path: {config.config_path}")
    print(f"  PUID:PGID = {config.puid}:{config.pgid}")
    dav_user = config.all.get("DAV_USER")
    dav_password = config.all.get("DAV_PASSWORD")
    warning = dav_auth_warning(dav_user, dav_password)
    if warning:
        print(f"  {warning}")

    print("\n[Phase 2] Creating directories...")
    DirectorySetup(config.data_path, config.config_path).create()
    print("  Directories created.")

    if migrate_prowlarr_config(config.v1_config_path, config.config_path):
        print("  Copied the v1 Prowlarr config.")

    print("\n[Phase 3] Launching Docker stack...")
    launcher = StackLauncher()
    launcher.start()
    launcher.wait_for_healthy()
    launcher.wait_for_config_files(config.config_path)

    print("\n[Phase 3b] Configuring authentication...")
    launcher.configure_auth(config.config_path)
    time.sleep(10)
    launcher.wait_for_healthy()

    print("\n[Phase 4] Configuring Prowlarr...")
    ProwlarrConfigurator(config.config_path).configure()

    ip = detect_ip()
    print("\n" + "=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print(f"\n  Phones (family catalog): http://{ip}:8888")
    print(f"  Prowlarr:                http://{ip}:9696")
    print("\nInfuse on Apple TV:")
    print("  1. Buy Infuse Pro (Family Sharing covers the household).")
    print("  2. Add share -> WebDAV (not SMB):")
    print(f"       address {ip}, port 8888, path /dav, name \"Cinema\"")
    for line in infuse_login_lines(dav_user, dav_password):
        print(line)
    print("  3. Settings -> Subtitles: auto-download, English.")


if __name__ == "__main__":
    main()
