# Cinema Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a Docker-based movie acquisition stack (Radarr + Prowlarr + qBittorrent + FlareSolverr) on a Synology NAS, fully configured via a Python setup script.

**Architecture:** Four Docker containers on a shared bridge network, orchestrated by docker-compose. A Python setup script creates directories, launches the stack, and configures all services via their REST APIs. Movies flow from torrent indexers → qBittorrent → Radarr (rename/organize) → Infuse via SMB.

**Tech Stack:** Docker Compose, Python 3 (stdlib only), REST APIs (Radarr v3, Prowlarr v1, qBittorrent Web API v2)

**Spec deviation:** The spec mentions auto-adding public indexers (1337x, TorrentGalaxy, LimeTorrents) to Prowlarr. This is intentionally omitted — Prowlarr's indexer schema IDs change between versions, making programmatic addition brittle. Adding indexers via the Prowlarr UI takes 30 seconds and is documented in the README as a manual step.

---

## File Structure

```
Cinema/
├── docker-compose.yml          # All four services
├── .env.example                # Template for user-specific values
├── setup.py                    # Automated setup & configuration script
├── README.md                   # Quick start guide & manual steps
└── tests/
    └── test_setup.py           # Unit tests for setup.py helper functions
```

`setup.py` internal modules (all in one file, organized as classes):

- `EnvConfig` — loads and validates `.env` values
- `DirectorySetup` — creates data and config directories
- `StackLauncher` — runs docker compose, waits for health
- `QBittorrentConfigurator` — configures qBittorrent via its Web API
- `RadarrConfigurator` — configures Radarr via its REST API
- `ProwlarrConfigurator` — configures Prowlarr via its REST API
- `main()` — orchestrates all phases

---

### Task 1: `.env.example` and `EnvConfig`

**Files:**
- Create: `.env.example`
- Create: `setup.py` (initial scaffold with `EnvConfig` class)
- Create: `tests/test_setup.py` (tests for `EnvConfig`)

- [ ] **Step 1: Create `.env.example`**

```env
# Synology user/group IDs (run `id` on your Synology to find these)
PUID=1000
PGID=1000

# Timezone
TZ=Europe/Zurich

# Path to your data volume on the Synology filesystem
DATA_PATH=/volume1/data

# Path for service config files (relative to this directory)
CONFIG_PATH=./config

# qBittorrent web UI password (change this!)
QB_PASSWORD=changeme123
```

- [ ] **Step 2: Write the failing test for EnvConfig**

```python
# tests/test_setup.py
import os
import tempfile
import unittest


class TestEnvConfig(unittest.TestCase):
    def test_loads_all_required_keys(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("PUID=1000\n")
            f.write("PGID=1000\n")
            f.write("TZ=Europe/Zurich\n")
            f.write("DATA_PATH=/volume1/data\n")
            f.write("CONFIG_PATH=./config\n")
            f.write("QB_PASSWORD=secret\n")
            env_path = f.name

        try:
            from setup import EnvConfig
            config = EnvConfig(env_path)
            self.assertEqual(config.puid, "1000")
            self.assertEqual(config.pgid, "1000")
            self.assertEqual(config.tz, "Europe/Zurich")
            self.assertEqual(config.data_path, "/volume1/data")
            self.assertEqual(config.config_path, "./config")
            self.assertEqual(config.qb_password, "secret")
        finally:
            os.unlink(env_path)

    def test_missing_key_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("PUID=1000\n")
            env_path = f.name

        try:
            from setup import EnvConfig
            with self.assertRaises(SystemExit):
                EnvConfig(env_path)
        finally:
            os.unlink(env_path)

    def test_strips_comments_and_whitespace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("# This is a comment\n")
            f.write("PUID=1000\n")
            f.write("PGID=1000\n")
            f.write("TZ=Europe/Zurich\n")
            f.write("DATA_PATH=/volume1/data  \n")
            f.write("CONFIG_PATH=./config\n")
            f.write("QB_PASSWORD=secret\n")
            env_path = f.name

        try:
            from setup import EnvConfig
            config = EnvConfig(env_path)
            self.assertEqual(config.data_path, "/volume1/data")
        finally:
            os.unlink(env_path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'setup'`

- [ ] **Step 4: Implement EnvConfig**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_setup.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add .env.example setup.py tests/test_setup.py
git commit -m "feat: add .env.example and EnvConfig loader"
```

---

### Task 2: `docker-compose.yml`

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write docker-compose.yml**

```yaml
services:
  qbittorrent:
    image: linuxserver/qbittorrent:latest
    container_name: qbittorrent
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - TZ=${TZ}
      - WEBUI_PORT=8080
    volumes:
      - ${CONFIG_PATH}/qbittorrent:/config
      - ${DATA_PATH}:/data
    ports:
      - "8080:8080"
      - "6881:6881"
      - "6881:6881/udp"
    restart: unless-stopped
    networks:
      - cinema

  radarr:
    image: linuxserver/radarr:latest
    container_name: radarr
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - TZ=${TZ}
    volumes:
      - ${CONFIG_PATH}/radarr:/config
      - ${DATA_PATH}:/data
    ports:
      - "7878:7878"
    restart: unless-stopped
    depends_on:
      - qbittorrent
    networks:
      - cinema

  prowlarr:
    image: linuxserver/prowlarr:latest
    container_name: prowlarr
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - TZ=${TZ}
    volumes:
      - ${CONFIG_PATH}/prowlarr:/config
    ports:
      - "9696:9696"
    restart: unless-stopped
    networks:
      - cinema

  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: flaresolverr
    environment:
      - LOG_LEVEL=info
      - TZ=${TZ}
    ports:
      - "8191:8191"
    restart: unless-stopped
    networks:
      - cinema

networks:
  cinema:
    driver: bridge
```

- [ ] **Step 2: Validate compose syntax**

Run: `docker compose config --quiet`
Expected: exits 0, no output (valid syntax). If docker compose isn't available locally, run `python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"` as a basic syntax check, or skip — the Synology will validate on deploy.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose.yml with all four services"
```

---

### Task 3: `DirectorySetup` and `StackLauncher`

**Files:**
- Modify: `setup.py` (add `DirectorySetup` and `StackLauncher` classes)
- Modify: `tests/test_setup.py` (add tests)

- [ ] **Step 1: Write failing tests for DirectorySetup**

Append to `tests/test_setup.py`:

```python
class TestDirectorySetup(unittest.TestCase):
    def test_creates_expected_directories(self):
        with tempfile.TemporaryDirectory() as data_dir:
            config_dir = os.path.join(data_dir, "config")
            from setup import DirectorySetup
            ds = DirectorySetup(data_path=data_dir, config_path=config_dir)
            ds.create()

            self.assertTrue(os.path.isdir(os.path.join(data_dir, "torrents", "movies")))
            self.assertTrue(os.path.isdir(os.path.join(data_dir, "media", "movies")))
            self.assertTrue(os.path.isdir(os.path.join(config_dir, "qbittorrent")))
            self.assertTrue(os.path.isdir(os.path.join(config_dir, "radarr")))
            self.assertTrue(os.path.isdir(os.path.join(config_dir, "prowlarr")))
            self.assertTrue(os.path.isdir(os.path.join(config_dir, "flaresolverr")))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as data_dir:
            config_dir = os.path.join(data_dir, "config")
            from setup import DirectorySetup
            ds = DirectorySetup(data_path=data_dir, config_path=config_dir)
            ds.create()
            ds.create()  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_setup.py::TestDirectorySetup -v`
Expected: FAIL — `ImportError: cannot import name 'DirectorySetup'`

- [ ] **Step 3: Implement DirectorySetup**

Append to `setup.py`:

```python
import os
import subprocess
import time
import json
import re
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlencode
from http.cookiejar import CookieJar


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_setup.py::TestDirectorySetup -v`
Expected: 2 tests PASS

- [ ] **Step 5: Implement StackLauncher**

Append to `setup.py`:

```python
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
```

- [ ] **Step 6: Commit**

```bash
git add setup.py tests/test_setup.py
git commit -m "feat: add DirectorySetup and StackLauncher"
```

---

### Task 4: `QBittorrentConfigurator`

**Files:**
- Modify: `setup.py` (add `QBittorrentConfigurator`)
- Modify: `tests/test_setup.py` (add tests for password parsing)

- [ ] **Step 1: Write failing test for temp password extraction**

Append to `tests/test_setup.py`:

```python
class TestQBittorrentConfigurator(unittest.TestCase):
    def test_extracts_temp_password_from_logs(self):
        from setup import QBittorrentConfigurator
        sample_logs = (
            "Some startup log line\n"
            "******** Information ********\n"
            "To control qBittorrent, access the WebUI at: http://localhost:8080\n"
            "The WebUI administrator password was not set. "
            "A temporary password is provided for this session: abc123XYZ\n"
            "You should set your own password in program preferences.\n"
        )
        password = QBittorrentConfigurator.parse_temp_password(sample_logs)
        self.assertEqual(password, "abc123XYZ")

    def test_raises_if_no_temp_password_found(self):
        from setup import QBittorrentConfigurator
        with self.assertRaises(ValueError):
            QBittorrentConfigurator.parse_temp_password("no password here\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_setup.py::TestQBittorrentConfigurator -v`
Expected: FAIL — `ImportError: cannot import name 'QBittorrentConfigurator'`

- [ ] **Step 3: Implement QBittorrentConfigurator**

Append to `setup.py`:

```python
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
            ["docker", "logs", "qbittorrent"],
            capture_output=True, text=True,
        )
        return self.parse_temp_password(result.stdout + result.stderr)

    def _request(self, path: str, data: dict | None = None) -> bytes:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_setup.py::TestQBittorrentConfigurator -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add setup.py tests/test_setup.py
git commit -m "feat: add QBittorrentConfigurator with API setup"
```

---

### Task 5: `RadarrConfigurator`

**Files:**
- Modify: `setup.py` (add `RadarrConfigurator`)
- Modify: `tests/test_setup.py` (add tests for API key parsing)

- [ ] **Step 1: Write failing test for API key extraction**

Append to `tests/test_setup.py`:

```python
class TestRadarrConfigurator(unittest.TestCase):
    def test_reads_api_key_from_config_xml(self):
        with tempfile.TemporaryDirectory() as d:
            config_xml = os.path.join(d, "config.xml")
            with open(config_xml, "w") as f:
                f.write('<?xml version="1.0" encoding="utf-8"?>\n')
                f.write("<Config>\n")
                f.write("  <ApiKey>test-api-key-12345</ApiKey>\n")
                f.write("  <Port>7878</Port>\n")
                f.write("</Config>\n")

            from setup import RadarrConfigurator
            api_key = RadarrConfigurator.read_api_key(config_xml)
            self.assertEqual(api_key, "test-api-key-12345")

    def test_raises_if_no_api_key(self):
        with tempfile.TemporaryDirectory() as d:
            config_xml = os.path.join(d, "config.xml")
            with open(config_xml, "w") as f:
                f.write('<?xml version="1.0" encoding="utf-8"?>\n')
                f.write("<Config><Port>7878</Port></Config>\n")

            from setup import RadarrConfigurator
            with self.assertRaises(ValueError):
                RadarrConfigurator.read_api_key(config_xml)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_setup.py::TestRadarrConfigurator -v`
Expected: FAIL — `ImportError: cannot import name 'RadarrConfigurator'`

- [ ] **Step 3: Implement RadarrConfigurator**

Append to `setup.py`:

```python
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

    def _request(self, method: str, path: str, body: dict | None = None):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_setup.py::TestRadarrConfigurator -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add setup.py tests/test_setup.py
git commit -m "feat: add RadarrConfigurator with API setup"
```

---

### Task 6: `ProwlarrConfigurator`

**Files:**
- Modify: `setup.py` (add `ProwlarrConfigurator`)
- Modify: `tests/test_setup.py` (add tests for API key parsing — reuses same XML format)

- [ ] **Step 1: Write failing test for Prowlarr API key extraction**

Append to `tests/test_setup.py`:

```python
class TestProwlarrConfigurator(unittest.TestCase):
    def test_reads_api_key_from_config_xml(self):
        with tempfile.TemporaryDirectory() as d:
            config_xml = os.path.join(d, "config.xml")
            with open(config_xml, "w") as f:
                f.write('<?xml version="1.0" encoding="utf-8"?>\n')
                f.write("<Config>\n")
                f.write("  <ApiKey>prowlarr-key-67890</ApiKey>\n")
                f.write("</Config>\n")

            from setup import ProwlarrConfigurator
            api_key = ProwlarrConfigurator.read_api_key(config_xml)
            self.assertEqual(api_key, "prowlarr-key-67890")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_setup.py::TestProwlarrConfigurator -v`
Expected: FAIL — `ImportError: cannot import name 'ProwlarrConfigurator'`

- [ ] **Step 3: Implement ProwlarrConfigurator**

Append to `setup.py`:

```python
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

    def _request(self, method: str, path: str, body: dict | None = None):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_setup.py::TestProwlarrConfigurator -v`
Expected: 1 test PASS

- [ ] **Step 5: Commit**

```bash
git add setup.py tests/test_setup.py
git commit -m "feat: add ProwlarrConfigurator with API setup"
```

---

### Task 7: `main()` Orchestrator

**Files:**
- Modify: `setup.py` (add `main()` function and CLI entry point)

- [ ] **Step 1: Implement main()**

Append to `setup.py`:

```python
def main():
    print("=" * 50)
    print("Cinema Stack Setup")
    print("=" * 50)

    # Phase 1: Environment
    print("\n[Phase 1] Loading configuration...")
    if not os.path.exists(".env"):
        if os.path.exists(".env.example"):
            print("No .env file found. Copying .env.example to .env")
            print("Please edit .env with your values and re-run this script.")
            import shutil
            shutil.copy(".env.example", ".env")
            sys.exit(0)
        else:
            print("No .env or .env.example found.")
            sys.exit(1)

    config = EnvConfig(".env")
    print(f"  Data path: {config.data_path}")
    print(f"  Config path: {config.config_path}")
    print(f"  PUID:PGID = {config.puid}:{config.pgid}")

    # Phase 2: Directories
    print("\n[Phase 2] Creating directories...")
    dirs = DirectorySetup(config.data_path, config.config_path)
    dirs.create()
    print("  Directories created.")

    # Phase 3: Stack launch
    print("\n[Phase 3] Launching Docker stack...")
    launcher = StackLauncher()
    launcher.start()
    launcher.wait_for_healthy()

    # Phase 4: Service configuration
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

    # Phase 5: Summary
    print("\n" + "=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print(f"\nService URLs (replace localhost with your Synology IP):")
    print(f"  Radarr:       http://localhost:7878")
    print(f"  Prowlarr:     http://localhost:9696")
    print(f"  qBittorrent:  http://localhost:8080")
    print(f"  FlareSolverr: http://localhost:8191")
    print(f"\nqBittorrent credentials: admin / <your QB_PASSWORD from .env>")
    print(f"\nRemaining manual steps:")
    print(f"  1. Open Prowlarr and add torrent indexers (1337x, etc.)")
    print(f"  2. On Apple TV: open Infuse → add SMB share →")
    print(f"     point to {config.data_path}/media/movies")
    print(f"  3. On iOS: install LunaSea, add Radarr server")
    print(f"     (or use Radarr web UI in browser on any device)")
    print(f"  4. Optional: adjust quality profiles in Radarr")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script syntax**

Run: `python -c "import setup; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add setup.py
git commit -m "feat: add main() orchestrator for full setup flow"
```

---

### Task 8: `README.md`

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README.md**

```markdown
# Cinema Stack

Movie download and playback workflow for Synology NAS + Apple TV (Infuse).

Browse movies on your phone, add them to your wishlist, and they automatically download, rename, and appear in Infuse.

## Prerequisites

- Synology NAS with Docker (Container Manager) installed
- SSH access to your Synology
- Python 3 (pre-installed on most Synology models)

## Quick Start

```bash
ssh your-synology
git clone <repo-url> Cinema
cd Cinema
cp .env.example .env
nano .env              # edit PUID, PGID, DATA_PATH, QB_PASSWORD
python3 setup.py
```

The setup script will:
1. Create the directory structure on your NAS
2. Start all Docker containers
3. Configure qBittorrent, Radarr, and Prowlarr automatically

## Services

| Service | URL | Purpose |
|---------|-----|---------|
| Radarr | `http://<NAS-IP>:7878` | Movie wishlist & manager |
| Prowlarr | `http://<NAS-IP>:9696` | Torrent indexer manager |
| qBittorrent | `http://<NAS-IP>:8080` | Torrent client |
| FlareSolverr | `http://<NAS-IP>:8191` | Cloudflare bypass |

## After Setup

1. **Add indexers** — Open Prowlarr (`:9696`) and add torrent indexers (e.g., 1337x)
2. **Configure Infuse** — On Apple TV, add an SMB share pointing to your movies folder
3. **Install LunaSea** (iOS) — Add your Radarr server for mobile browsing, or use the Radarr web UI in any browser

## Daily Workflow

1. Open LunaSea or Radarr web UI on your phone
2. Search for a movie → Add to wishlist
3. Movie downloads automatically and appears in Infuse

## Backup

Back up the `./config/` directory to preserve all service settings.

## Folder Structure

```
<DATA_PATH>/
├── torrents/movies/    # qBittorrent downloads
└── media/movies/       # Radarr organizes here → Infuse reads this
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup instructions"
```

---

### Task 9: Integration Test (End-to-End Dry Run)

**Files:**
- Modify: `tests/test_setup.py` (add integration smoke test)

- [ ] **Step 1: Write integration test that validates the full flow without Docker**

Append to `tests/test_setup.py`:

```python
class TestIntegration(unittest.TestCase):
    def test_full_config_loading_and_directory_creation(self):
        """End-to-end test: load .env, create dirs — everything except Docker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            data_path = os.path.join(tmpdir, "data")
            config_path = os.path.join(tmpdir, "config")
            os.makedirs(data_path)

            with open(env_path, "w") as f:
                f.write(f"PUID=1000\n")
                f.write(f"PGID=1000\n")
                f.write(f"TZ=Europe/Zurich\n")
                f.write(f"DATA_PATH={data_path}\n")
                f.write(f"CONFIG_PATH={config_path}\n")
                f.write(f"QB_PASSWORD=testpass\n")

            from setup import EnvConfig, DirectorySetup
            config = EnvConfig(env_path)
            ds = DirectorySetup(config.data_path, config.config_path)
            ds.create()

            self.assertTrue(os.path.isdir(os.path.join(data_path, "torrents", "movies")))
            self.assertTrue(os.path.isdir(os.path.join(data_path, "media", "movies")))
            self.assertTrue(os.path.isdir(os.path.join(config_path, "radarr")))
            self.assertEqual(config.qb_password, "testpass")
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_setup.py
git commit -m "test: add integration smoke test for config + directory setup"
```
