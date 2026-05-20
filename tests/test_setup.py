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
