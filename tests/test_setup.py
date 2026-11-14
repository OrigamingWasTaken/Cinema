import os
import tempfile
import unittest


def write_env(path, extra=None, omit=()):
    values = {
        "PUID": "1024",
        "PGID": "100",
        "TZ": "Europe/Zurich",
        "DATA_PATH": "/volume1/data",
        "CONFIG_PATH": "./config",
        "TMDB_TOKEN": "tmdb-secret",
    }
    values.update(extra or {})
    with open(path, "w") as f:
        for k, v in values.items():
            if k not in omit:
                f.write(f"{k}={v}\n")


class TestEnvConfig(unittest.TestCase):
    def test_loads_all_required_keys(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            write_env(env_path)
            from setup import EnvConfig
            config = EnvConfig(env_path)
            self.assertEqual(config.puid, "1024")
            self.assertEqual(config.pgid, "100")
            self.assertEqual(config.tz, "Europe/Zurich")
            self.assertEqual(config.data_path, "/volume1/data")
            self.assertEqual(config.config_path, "./config")
            self.assertEqual(config.tmdb_token, "tmdb-secret")
            self.assertIsNone(config.v1_config_path)
            self.assertFalse(hasattr(config, "qb_password"))

    def test_v1_config_path_optional_and_empty_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            from setup import EnvConfig
            write_env(env_path, {"V1_CONFIG_PATH": "/volume1/v1/config"})
            self.assertEqual(EnvConfig(env_path).v1_config_path, "/volume1/v1/config")
            write_env(env_path, {"V1_CONFIG_PATH": ""})
            self.assertIsNone(EnvConfig(env_path).v1_config_path)

    def test_missing_key_raises(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            write_env(env_path, omit=("TMDB_TOKEN",))
            from setup import EnvConfig
            with self.assertRaises(SystemExit):
                EnvConfig(env_path)

    def test_empty_tmdb_token_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            write_env(env_path, {"TMDB_TOKEN": ""})
            from setup import EnvConfig
            with self.assertRaises(SystemExit):
                EnvConfig(env_path)

    def test_strips_comments_and_whitespace(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            write_env(env_path, {"DATA_PATH": "/volume1/data  "})
            with open(env_path, "a") as f:
                f.write("# a comment\n")
            from setup import EnvConfig
            self.assertEqual(EnvConfig(env_path).data_path, "/volume1/data")


class TestDirectorySetup(unittest.TestCase):
    def test_creates_expected_directories(self):
        with tempfile.TemporaryDirectory() as data_dir:
            config_dir = os.path.join(data_dir, "config")
            from setup import DirectorySetup
            DirectorySetup(data_path=data_dir, config_path=config_dir).create()
            self.assertTrue(os.path.isdir(os.path.join(data_dir, "movies")))
            for sub in ("prowlarr", "rqbit/db", "rqbit/cache", "cinema"):
                self.assertTrue(os.path.isdir(os.path.join(config_dir, sub)), sub)
            self.assertFalse(os.path.exists(os.path.join(config_dir, "radarr")))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as data_dir:
            config_dir = os.path.join(data_dir, "config")
            from setup import DirectorySetup
            ds = DirectorySetup(data_path=data_dir, config_path=config_dir)
            ds.create()
            ds.create()


class TestMigrate(unittest.TestCase):
    def _v1(self, root):
        v1 = os.path.join(root, "v1")
        os.makedirs(os.path.join(v1, "prowlarr"))
        with open(os.path.join(v1, "prowlarr", "config.xml"), "w") as f:
            f.write("<Config><ApiKey>old</ApiKey></Config>")
        return v1

    def test_migrate_copies_when_empty(self):
        from setup import migrate_prowlarr_config
        with tempfile.TemporaryDirectory() as root:
            v1 = self._v1(root)
            cfg = os.path.join(root, "config")
            os.makedirs(os.path.join(cfg, "prowlarr"))
            self.assertTrue(migrate_prowlarr_config(v1, cfg))
            with open(os.path.join(cfg, "prowlarr", "config.xml")) as f:
                self.assertIn("old", f.read())

    def test_migrate_skips_when_present(self):
        from setup import migrate_prowlarr_config
        with tempfile.TemporaryDirectory() as root:
            v1 = self._v1(root)
            cfg = os.path.join(root, "config")
            os.makedirs(os.path.join(cfg, "prowlarr"))
            target = os.path.join(cfg, "prowlarr", "config.xml")
            with open(target, "w") as f:
                f.write("<Config><ApiKey>new</ApiKey></Config>")
            self.assertFalse(migrate_prowlarr_config(v1, cfg))
            with open(target) as f:
                self.assertIn("new", f.read())

    def test_migrate_noop_without_v1(self):
        from setup import migrate_prowlarr_config
        with tempfile.TemporaryDirectory() as root:
            cfg = os.path.join(root, "config")
            os.makedirs(os.path.join(cfg, "prowlarr"))
            self.assertFalse(migrate_prowlarr_config(None, cfg))
            self.assertFalse(migrate_prowlarr_config(os.path.join(root, "nope"), cfg))
            self.assertFalse(os.path.exists(os.path.join(cfg, "prowlarr", "config.xml")))


class TestPlans(unittest.TestCase):
    def test_plan_adds_proxy_and_removes_radarr(self):
        from setup import plan_prowlarr_changes
        add, remove = plan_prowlarr_changes(
            proxies=[],
            applications=[
                {"id": 3, "implementation": "Radarr"},
                {"id": 4, "implementation": "Sonarr"},
                {"id": 7, "implementation": "Radarr"},
            ],
        )
        self.assertTrue(add)
        self.assertEqual(remove, [3, 7])

    def test_plan_idempotent(self):
        from setup import plan_prowlarr_changes
        add, remove = plan_prowlarr_changes(
            proxies=[{"id": 1, "implementation": "FlareSolverr"}], applications=[]
        )
        self.assertFalse(add)
        self.assertEqual(remove, [])

    def test_plan_indexers_empty_adds_three(self):
        from setup import plan_indexers
        self.assertEqual(plan_indexers([]), ["The Pirate Bay", "YTS", "Knaben"])

    def test_plan_indexers_skips_when_any_exist(self):
        from setup import plan_indexers
        self.assertEqual(plan_indexers([{"id": 1, "name": "YTS"}]), [])


class TestProwlarrConfigurator(unittest.TestCase):
    def test_reads_api_key_from_config_xml(self):
        with tempfile.TemporaryDirectory() as d:
            config_xml = os.path.join(d, "config.xml")
            with open(config_xml, "w") as f:
                f.write("<Config><ApiKey>prowlarr-key-67890</ApiKey></Config>\n")
            from setup import ProwlarrConfigurator
            self.assertEqual(ProwlarrConfigurator.read_api_key(config_xml), "prowlarr-key-67890")

    def test_raises_if_no_api_key(self):
        with tempfile.TemporaryDirectory() as d:
            config_xml = os.path.join(d, "config.xml")
            with open(config_xml, "w") as f:
                f.write("<Config><Port>9696</Port></Config>\n")
            from setup import ProwlarrConfigurator
            with self.assertRaises(ValueError):
                ProwlarrConfigurator.read_api_key(config_xml)


class TestHelpers(unittest.TestCase):
    def test_constants(self):
        import setup
        self.assertEqual(setup.DATA_DIRS, ["movies"])
        self.assertEqual(setup.CONFIG_SERVICES, ["prowlarr", "rqbit/db", "rqbit/cache", "cinema"])
        self.assertEqual(setup.StackLauncher.HEALTH_PORTS, {"prowlarr": 9696, "cinema": 8888})

    def test_docker_bin_falls_back(self):
        from unittest import mock
        import setup
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(setup.docker_bin(), "/usr/local/bin/docker")
        with mock.patch("shutil.which", return_value="/usr/bin/docker"):
            self.assertEqual(setup.docker_bin(), "/usr/bin/docker")


if __name__ == "__main__":
    unittest.main()
