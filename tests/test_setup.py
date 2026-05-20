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
            ds.create()


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


if __name__ == "__main__":
    unittest.main()
