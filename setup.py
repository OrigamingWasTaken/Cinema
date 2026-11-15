#!/usr/bin/env python3
"""Cinema Stack v2 — automated setup and configuration (stdlib only)."""
import json
import os
import shutil
import socket
import stat
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


DATA_DIRS = ["movies", "downloads", "shows"]
CONFIG_SERVICES = ["prowlarr", "rqbit/db", "rqbit/cache", "cinema", "sonarr"]

DEFAULT_INDEXERS = ["The Pirate Bay", "YTS", "Knaben"]
# EZTV and 1337x are not used: EZTV is unreliable and 1337x needs a Cloudflare solve.
TV_INDEXERS = ["showRSS", "LimeTorrents", "TorrentDownload", "Nyaa.si"]
SONARR_SYNC_CATEGORIES = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5070]


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


def chown_tree(path: str, uid, gid):
    """Recursively give path to uid:gid (numeric). Skips symlinks and correct owners."""
    uid, gid = int(uid), int(gid)

    def fix(p: str):
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            return
        if st.st_uid != uid or st.st_gid != gid:
            os.lchown(p, uid, gid)

    if not os.path.lexists(path):
        return
    fix(path)
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            fix(os.path.join(root, name))


def give_to_user(config: "EnvConfig", services: tuple) -> None:
    """chown the data dirs and these services' config dirs to PUID:PGID (idempotent)."""
    for d in DATA_DIRS:
        chown_tree(os.path.join(config.data_path, d), config.puid, config.pgid)
    for svc in services:
        chown_tree(os.path.join(config.config_path, svc), config.puid, config.pgid)


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
        files = [os.path.join(config_path, svc, "config.xml") for svc in ("prowlarr", "sonarr")]
        print("Waiting for Prowlarr and Sonarr to generate their config files...")
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


def plan_tv_indexers(existing: list) -> list:
    """Names of the TV indexers Prowlarr does not have yet."""
    have = {i.get("name") for i in existing}
    return [n for n in TV_INDEXERS if n not in have]


class ArrClient:
    """Shared HTTP plumbing for the Prowlarr and Sonarr APIs (subclasses set the class constants)."""
    NAME = ""
    BASE_URL = ""
    API_PREFIX = ""
    READY_TIMEOUT = 60

    @staticmethod
    def read_api_key(config_xml: str) -> str:
        tree = ET.parse(config_xml)
        el = tree.find("ApiKey")
        if el is None or not el.text:
            raise ValueError(f"No ApiKey found in {config_xml}")
        return el.text

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        url = f"{self.BASE_URL}{self.API_PREFIX}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = Request(url, data=data, method=method)
        req.add_header("X-Api-Key", self.api_key)
        req.add_header("Content-Type", "application/json")
        # Generous timeout: forceSave calls can wait on slow upstreams.
        resp = urlopen(req, timeout=60)
        content = resp.read()
        return json.loads(content) if content else None

    def wait_until_ready(self, timeout: Optional[int] = None, interval: float = 3):
        """Poll /system/status: docker-proxy accepts TCP before the app is up."""
        timeout = self.READY_TIMEOUT if timeout is None else timeout
        deadline = time.time() + timeout
        while True:
            try:
                self._request("GET", "/system/status")
                return
            except Exception as e:
                if time.time() >= deadline:
                    print(f"  WARNING: {self.NAME} API not ready after {timeout}s: {e}")
                    return
                time.sleep(interval)

    @staticmethod
    def _set_fields(schema: dict, values: dict) -> list:
        return [dict(f, value=values[f["name"]]) if f["name"] in values else f
                for f in schema.get("fields", [])]


class ProwlarrConfigurator(ArrClient):
    NAME = "Prowlarr"
    BASE_URL = "http://localhost:9696"
    API_PREFIX = "/api/v1"

    def __init__(self, config_path: str):
        config_xml = os.path.join(config_path, "prowlarr", "config.xml")
        self.api_key = self.read_api_key(config_xml)

    def _flaresolverr_tag_id(self) -> int:
        for tag in self._request("GET", "/tag"):
            if tag["label"] == "flaresolverr":
                return tag["id"]
        return self._request("POST", "/tag", {"label": "flaresolverr"})["id"]

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

    def add_sonarr_application(self, sonarr_api_key: str):
        applications = self._request("GET", "/applications")
        if any(a.get("implementation") == "Sonarr" for a in applications):
            print("  Sonarr application already present in Prowlarr")
            return
        try:
            schemas = self._request("GET", "/applications/schema")
            schema = next((s for s in schemas if s.get("implementation") == "Sonarr"), None)
            if schema is None:
                print("  WARNING: Sonarr not in Prowlarr's application schema")
                return
            values = {
                "prowlarrUrl": "http://prowlarr:9696",
                "baseUrl": "http://sonarr:8989",
                "apiKey": sonarr_api_key,
                "syncCategories": SONARR_SYNC_CATEGORIES,
            }
            body = dict(schema, name="Sonarr", syncLevel="fullSync", tags=[],
                        fields=self._set_fields(schema, values))
            self._request("POST", "/applications?forceSave=true", body)
            print("  Sonarr application added to Prowlarr")
        except Exception as e:
            print(f"  WARNING: could not add the Sonarr application: {e}")

    def add_tv_indexers(self):
        wanted = plan_tv_indexers(self._request("GET", "/indexer"))
        if wanted:
            self._add_indexers(wanted)
        else:
            print("  TV indexers already present")

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


FRENCH_REGEX = r"\b(TRUE)?FRENCH\b|\bVF[FQI]?\b"
MULTI_REGEX = r"\bMULTI\b"
PROFILE_QUALITIES = ["HDTV-720p", "HDTV-1080p", "WEBRip-720p", "WEBRip-1080p", "WEBDL-720p",
                     "WEBDL-1080p", "Bluray-720p", "Bluray-1080p"]
PROFILE_CUTOFF = "WEBDL-1080p"
FRENCH_ONLY_SCORE = -10000


class SonarrConfigurator(ArrClient):
    NAME = "Sonarr"
    BASE_URL = "http://localhost:8989"
    API_PREFIX = "/api/v3"
    READY_TIMEOUT = 120

    def __init__(self, config_path: str, puid: str, pgid: str):
        config_xml = os.path.join(config_path, "sonarr", "config.xml")
        self.api_key = self.read_api_key(config_xml)
        self.puid = puid
        self.pgid = pgid

    def _ensure_root_folder(self):
        if any(r.get("path", "").rstrip("/") == "/data/shows"
               for r in self._request("GET", "/rootfolder")):
            print("  Sonarr root folder already set")
            return
        self._request("POST", "/rootfolder", {"path": "/data/shows"})
        print("  Sonarr root folder added: /data/shows")

    def _ensure_download_client(self):
        if any(c.get("implementation") == "RQBit"
               for c in self._request("GET", "/downloadclient")):
            print("  Sonarr download client already present")
        else:
            schemas = self._request("GET", "/downloadclient/schema")
            schema = next((s for s in schemas if s.get("implementation") == "RQBit"), None)
            if schema is None:
                print("  WARNING: RQBit not in Sonarr's download client schema")
            else:
                body = dict(
                    schema, name="rqbit", enable=True, priority=1, tags=[],
                    removeCompletedDownloads=False, removeFailedDownloads=True,
                    fields=self._set_fields(schema, {
                        "host": "rqbit", "port": 3030, "useSsl": False, "urlBase": "/"}))
                self._request("POST", "/downloadclient", body)
                print("  Sonarr download client added: rqbit")
        # Completed download handling is what imports finished torrents.
        conf = self._request("GET", "/config/downloadclient")
        if not conf.get("enableCompletedDownloadHandling"):
            self._request("PUT", f"/config/downloadclient/{conf['id']}",
                          dict(conf, enableCompletedDownloadHandling=True))
            print("  Sonarr completed download handling enabled")

    def _ensure_hardlinks(self):
        conf = self._request("GET", "/config/mediamanagement")
        if conf.get("copyUsingHardlinks"):
            return
        self._request("PUT", f"/config/mediamanagement/{conf['id']}",
                      dict(conf, copyUsingHardlinks=True))
        print("  Sonarr set to import with hardlinks")

    def _ensure_custom_format(self) -> int:
        for cf in self._request("GET", "/customformat"):
            if cf.get("name") == "French only":
                return cf["id"]
        schemas = self._request("GET", "/customformat/schema")
        schema = next(s for s in schemas if s.get("implementation") == "ReleaseTitleSpecification")

        def spec(name, regex, negate):
            return dict(schema, name=name, negate=negate, required=True,
                        fields=self._set_fields(schema, {"value": regex}))

        created = self._request("POST", "/customformat", {
            "name": "French only",
            "includeCustomFormatWhenRenaming": False,
            "specifications": [spec("French", FRENCH_REGEX, False),
                               spec("Not MULTI", MULTI_REGEX, True)],
        })
        print("  Sonarr custom format added: French only")
        return created["id"]

    def _ensure_profile(self, name: str, french_score: int, format_id: int):
        if any(p.get("name") == name for p in self._request("GET", "/qualityprofile")):
            print(f"  Sonarr quality profile already present: {name}")
            return
        schema = self._request("GET", "/qualityprofile/schema")
        cutoff = None
        items = []
        for item in schema["items"]:
            item = dict(item)
            children = item.get("items") or []
            names = [c["quality"]["name"] for c in children] if children else \
                [item["quality"]["name"]]
            item["allowed"] = any(n in PROFILE_QUALITIES for n in names)
            if children:
                item["items"] = [dict(c, allowed=c["quality"]["name"] in PROFILE_QUALITIES)
                                 for c in children]
            if PROFILE_CUTOFF in names:
                cutoff = item["id"] if children else item["quality"]["id"]
            items.append(item)
        if cutoff is None:
            print(f"  WARNING: {PROFILE_CUTOFF} not in Sonarr's quality list; skipping {name}")
            return
        format_items = [dict(f, score=french_score if f.get("format") == format_id else 0)
                        for f in schema.get("formatItems", [])]
        if not any(f.get("format") == format_id for f in format_items):
            format_items.append({"format": format_id, "name": "French only",
                                 "score": french_score})
        self._request("POST", "/qualityprofile", dict(
            schema, name=name, upgradeAllowed=False, cutoff=cutoff, items=items,
            minFormatScore=0, cutoffFormatScore=0, formatItems=format_items))
        print(f"  Sonarr quality profile added: {name}")

    def configure(self):
        self.wait_until_ready()
        self._ensure_root_folder()
        self._ensure_download_client()
        self._ensure_hardlinks()
        format_id = self._ensure_custom_format()
        self._ensure_profile("Cinema", FRENCH_ONLY_SCORE, format_id)
        self._ensure_profile("Cinema FR", 0, format_id)


HARDLINK_TEST_NAME = ".cinema-link-test"


def verify_hardlinks(data_path: str, puid: str, pgid: str) -> Optional[str]:
    """None when Sonarr (as PUID:PGID) can hardlink downloads/ into shows/ and rqbit runs as
    that user; otherwise a one-line reason naming the failed check."""
    src = os.path.join(data_path, "downloads", HARDLINK_TEST_NAME)
    dst = os.path.join(data_path, "shows", HARDLINK_TEST_NAME)
    want = f"{puid}:{pgid}"

    def remove_both():
        for p in (src, dst):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass

    try:
        remove_both()
        with open(src, "w") as f:
            f.write("cinema link test")
        os.chown(src, int(puid), int(pgid))
        ln = subprocess.run(
            ["sudo", docker_bin(), "exec", "-u", want, "sonarr", "ln",
             f"/data/downloads/{HARDLINK_TEST_NAME}", f"/data/shows/{HARDLINK_TEST_NAME}"],
            capture_output=True, text=True)
        if ln.returncode != 0:
            detail = (ln.stderr or "").strip() or f"exit code {ln.returncode}"
            return (f"ln failed as {want} inside the sonarr container: {detail} "
                    "(is sonarr running, and are downloads/ and shows/ writable by that user?)")
        if os.stat(src).st_ino != os.stat(dst).st_ino:
            return "the link has a different inode (the file was copied, not linked)"
        inspect = subprocess.run(
            ["sudo", docker_bin(), "inspect", "-f", "{{.Config.User}}", "rqbit"],
            capture_output=True, text=True)
        if inspect.returncode != 0:
            return f"could not inspect the rqbit container: {(inspect.stderr or '').strip()}"
        got = inspect.stdout.strip()
        if got != want:
            return f"rqbit runs as '{got}', expected PUID:PGID ({want})"
        return None
    except OSError as e:
        return f"could not run the hardlink test: {e}"
    finally:
        remove_both()


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
    give_to_user(config, ("rqbit", "sonarr"))

    if migrate_prowlarr_config(config.v1_config_path, config.config_path):
        print("  Copied the v1 Prowlarr config.")

    print("\n[Phase 3] Launching Docker stack...")
    launcher = StackLauncher()
    launcher.start()
    # The old root rqbit may have written more until `up` replaced it.
    give_to_user(config, ("rqbit",))
    launcher.wait_for_healthy()
    launcher.wait_for_config_files(config.config_path)

    print("\n[Phase 3b] Configuring authentication...")
    launcher.configure_auth(config.config_path)
    time.sleep(10)
    launcher.wait_for_healthy()

    print("\n[Phase 4] Configuring Prowlarr...")
    prowlarr = ProwlarrConfigurator(config.config_path)
    prowlarr.configure()

    print("\n[Phase 5] Configuring Sonarr...")
    sonarr = SonarrConfigurator(config.config_path, config.puid, config.pgid)
    sonarr.configure()
    prowlarr.add_sonarr_application(sonarr.api_key)
    prowlarr.add_tv_indexers()

    print("\n[Phase 6] Checking hardlinks...")
    hardlink_problem = verify_hardlinks(config.data_path, config.puid, config.pgid)
    if hardlink_problem is None:
        print("  Hardlinks work.")

    ip = detect_ip()
    print("\n" + "=" * 50)
    print("Setup complete!")
    print("=" * 50)
    print(f"\n  Phones (family catalog): http://{ip}:8888")
    print(f"  Prowlarr (admin only):   http://{ip}:9696")
    print(f"  Sonarr (admin only):     http://{ip}:8989")
    print("\nInfuse on Apple TV:")
    print("  1. Buy Infuse Pro (Family Sharing covers the household).")
    print("  2. Add share -> WebDAV (not SMB):")
    print(f"       address {ip}, port 8888, path /dav, name \"Cinema\"")
    for line in infuse_login_lines(dav_user, dav_password):
        print(line)
    print("  3. Settings -> Subtitles: auto-download, English.")
    print("  Infuse: Shows appear in the same share")
    if hardlink_problem is not None:
        print()
        print(f"ERROR: hardlinks from downloads/ to shows/ do not work: {hardlink_problem}")
        print("Imports would copy files. The stack is up; fix this and run setup again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
