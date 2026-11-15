import contextlib
import io
import stat
import os
import subprocess
import tempfile
import unittest
from unittest import mock


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
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                EnvConfig(env_path)

    def test_empty_tmdb_token_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            env_path = os.path.join(d, ".env")
            write_env(env_path, {"TMDB_TOKEN": ""})
            from setup import EnvConfig
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
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
            for sub in ("prowlarr", "rqbit/db", "rqbit/cache", "cinema", "sonarr"):
                self.assertTrue(os.path.isdir(os.path.join(config_dir, sub)), sub)
            self.assertFalse(os.path.exists(os.path.join(config_dir, "radarr")))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as data_dir:
            config_dir = os.path.join(data_dir, "config")
            from setup import DirectorySetup
            ds = DirectorySetup(data_path=data_dir, config_path=config_dir)
            ds.create()
            ds.create()


def docker_ps(stdout=""):
    return mock.patch("subprocess.run",
                      return_value=subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""))


class TestMigrate(unittest.TestCase):
    def setUp(self):
        patcher = docker_ps()
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

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


    def test_migrate_failure_leaves_no_config_xml_and_exits(self):
        from setup import migrate_prowlarr_config
        with tempfile.TemporaryDirectory() as root:
            v1 = self._v1(root)
            with open(os.path.join(v1, "prowlarr", "other.db"), "w") as f:
                f.write("x")
            cfg = os.path.join(root, "config")
            os.makedirs(os.path.join(cfg, "prowlarr"))
            real = __import__("shutil").copyfile

            def flaky(src, dst, *a, **k):
                if os.path.basename(src) == "other.db":
                    raise PermissionError("denied")
                return real(src, dst, *a, **k)

            out = io.StringIO()
            with mock.patch("shutil.copyfile", side_effect=flaky), \
                    contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                migrate_prowlarr_config(v1, cfg)
            self.assertFalse(os.path.exists(os.path.join(cfg, "prowlarr", "config.xml")))
            self.assertIn("sudo python3 setup.py", out.getvalue())

    def test_migrate_refuses_while_prowlarr_runs(self):
        import setup
        with tempfile.TemporaryDirectory() as root:
            v1 = self._v1(root)
            cfg = os.path.join(root, "config")
            os.makedirs(os.path.join(cfg, "prowlarr"))
            out = io.StringIO()
            with docker_ps("3f2a9c\n") as run, mock.patch("setup.docker_bin",
                                                       return_value="/bin/docker"), \
                    contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                setup.migrate_prowlarr_config(v1, cfg)
            run.assert_called_once()
            self.assertEqual(run.call_args[0][0],
                             ["sudo", "/bin/docker", "ps", "-q", "-f", "name=^prowlarr$"])
            self.assertFalse(os.path.exists(os.path.join(cfg, "prowlarr", "config.xml")))
            self.assertIn("sudo docker compose down", out.getvalue())
            self.assertIn("sudo python3 setup.py", out.getvalue())

    def test_migrate_checks_docker_only_when_copying(self):
        from setup import migrate_prowlarr_config
        with tempfile.TemporaryDirectory() as root:
            v1 = self._v1(root)
            cfg = os.path.join(root, "config")
            os.makedirs(os.path.join(cfg, "prowlarr"))
            self.assertFalse(migrate_prowlarr_config(None, cfg))
            self.run.assert_not_called()
            self.assertTrue(migrate_prowlarr_config(v1, cfg))  # nothing running: copies
            self.assertEqual(self.run.call_count, 1)
            self.assertFalse(migrate_prowlarr_config(v1, cfg))  # already migrated
            self.assertEqual(self.run.call_count, 1)

    def test_migrate_false_when_source_has_no_config_xml(self):
        from setup import migrate_prowlarr_config
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "v1", "prowlarr"))
            cfg = os.path.join(root, "config")
            os.makedirs(os.path.join(cfg, "prowlarr"))
            self.assertFalse(migrate_prowlarr_config(os.path.join(root, "v1"), cfg))


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


class TestConfigure(unittest.TestCase):
    def _configurator(self, indexers, fail_path=None, fail_name=None):
        import setup
        cfg = setup.ProwlarrConfigurator.__new__(setup.ProwlarrConfigurator)
        cfg.api_key = "k"
        calls = []
        schemas = [{"name": n, "implementation": n} for n in setup.DEFAULT_INDEXERS]

        def fake(method, path, body=None):
            calls.append((method, path, body))
            if method == "POST" and fail_name and body and body.get("name") == fail_name:
                raise OSError("boom")
            if (method, path) == ("GET", "/system/status"):
                return {}
            if (method, path) == ("GET", "/indexerProxy"):
                return []
            if (method, path) == ("GET", "/applications"):
                return [{"id": 5, "implementation": "Radarr"}]
            if (method, path) == ("GET", "/tag"):
                return []
            if (method, path) == ("POST", "/tag"):
                return {"id": 9}
            if (method, path) == ("GET", "/indexer"):
                return indexers
            if (method, path) == ("GET", "/indexer/schema"):
                return schemas
            return None

        cfg._request = fake
        return cfg, calls

    def test_configure_applies_plan(self):
        cfg, calls = self._configurator([])
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.configure()
        proxy = [c for c in calls if c[1] == "/indexerProxy?forceSave=true"]
        self.assertEqual(len(proxy), 1)
        self.assertEqual(proxy[0][0], "POST")
        self.assertEqual(proxy[0][2]["tags"], [9])
        self.assertIn(("DELETE", "/applications/5", None), calls)
        posted = [c[2] for c in calls if c[1] == "/indexer?forceSave=true"]
        self.assertEqual([p["name"] for p in posted] if "name" in posted[0] else
                         [p["implementation"] for p in posted],
                         ["The Pirate Bay", "YTS", "Knaben"])
        for p in posted:
            self.assertIs(p["enable"], True)
            self.assertEqual(p["appProfileId"], 1)
            self.assertEqual(p["priority"], 25)
            self.assertEqual(p["tags"], [])

    def test_configure_skips_indexers_when_any_exist(self):
        cfg, calls = self._configurator([{"id": 1, "name": "YTS"}])
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.configure()
        self.assertFalse([c for c in calls if c[1].startswith("/indexer?")])

    def test_failing_indexer_warns_and_continues(self):
        cfg, calls = self._configurator([], fail_name="YTS")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cfg.configure()
        self.assertEqual(len([c for c in calls if c[1] == "/indexer?forceSave=true"]), 3)
        self.assertIn("WARNING: could not add indexer YTS", out.getvalue())

    def test_missing_schema_warns(self):
        import setup
        cfg, calls = self._configurator([])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cfg._add_indexers(["Nope"])
        self.assertIn("Nope not in Prowlarr schema", out.getvalue())

    def test_wait_until_ready_retries(self):
        import setup
        cfg = setup.ProwlarrConfigurator.__new__(setup.ProwlarrConfigurator)
        attempts = []

        def fake(method, path, body=None):
            attempts.append(path)
            if len(attempts) < 3:
                raise OSError("down")

        cfg._request = fake
        cfg.wait_until_ready(timeout=5, interval=0)
        self.assertEqual(len(attempts), 3)


class TestHelpers(unittest.TestCase):
    def test_constants(self):
        import setup
        self.assertEqual(setup.DATA_DIRS, ["movies", "downloads", "shows"])
        self.assertEqual(setup.CONFIG_SERVICES,
                         ["prowlarr", "rqbit/db", "rqbit/cache", "cinema", "sonarr", "tailscale"])
        self.assertEqual(setup.StackLauncher.HEALTH_PORTS, {"prowlarr": 9696, "cinema": 8888})

    def test_docker_bin_falls_back(self):
        import setup
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(setup.docker_bin(), "/usr/local/bin/docker")
        with mock.patch("shutil.which", return_value="/usr/bin/docker"):
            self.assertEqual(setup.docker_bin(), "/usr/bin/docker")


class TestDavHint(unittest.TestCase):
    def test_login_only_when_both_set(self):
        from setup import dav_auth_warning, infuse_login_lines
        self.assertIn("username u", infuse_login_lines("u", "p")[0])
        self.assertIsNone(dav_auth_warning("u", "p"))
        self.assertIsNone(dav_auth_warning("", None))
        self.assertEqual(infuse_login_lines("", ""),
                         ["       leave username and password empty"])
        for user, password in (("u", ""), ("", "p")):
            self.assertIn("only one of DAV_USER / DAV_PASSWORD", dav_auth_warning(user, password))
            lines = infuse_login_lines(user, password)
            self.assertIn("leave username and password empty", lines[0])
            self.assertIn("WARNING", lines[1])


class TestCompose(unittest.TestCase):
    def test_cinema_gets_optional_prowlarr_api_key(self):
        path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
        with open(path) as f:
            self.assertIn("- PROWLARR_API_KEY=${PROWLARR_API_KEY:-}", f.read())


class TestRqbitCa(unittest.TestCase):
    def test_compose_mounts_readable_ca_and_pins_same_image(self):
        import setup
        path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
        with open(path) as f:
            text = f.read()
        self.assertIn("- ${CONFIG_PATH}/rqbit/cacerts.pem:/etc/ssl/cacerts.pem:ro", text)
        self.assertIn("image: " + setup.RQBIT_IMAGE, text)

    def test_copies_ca_out_of_image_world_readable(self):
        import setup
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "rqbit", "cacerts.pem")

            def fake_run(cmd, **kw):
                if cmd[2] == "create":
                    return mock.Mock(returncode=0, stdout="cid\n", stderr="")
                if cmd[2] == "cp":
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(cmd[4], "w") as f:
                        f.write("CERT")
                    os.chmod(cmd[4], 0o600)
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run) as run, \
                    mock.patch("os.chown") as chown:
                setup.install_rqbit_ca(d, "1024", "100")
            cmds = [c.args[0][2] for c in run.call_args_list]
            self.assertEqual(cmds, ["create", "cp", "rm"])
            self.assertEqual(run.call_args_list[0].args[0][-1], setup.RQBIT_IMAGE)
            self.assertEqual(run.call_args_list[1].args[0][3], "cid:/etc/ssl/cacerts.pem")
            self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o644)
            chown.assert_called_once_with(dest, 1024, 100)

    def test_skips_when_a_readable_copy_exists(self):
        import setup
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "rqbit", "cacerts.pem")
            os.makedirs(os.path.dirname(dest))
            with open(dest, "w") as f:
                f.write("CERT")
            os.chmod(dest, 0o644)
            with mock.patch("subprocess.run") as run, mock.patch("os.chown"):
                setup.install_rqbit_ca(d, "1024", "100")
            run.assert_not_called()

    def test_failure_exits_with_message(self):
        import setup
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("subprocess.run",
                           return_value=mock.Mock(returncode=1, stdout="", stderr="boom")), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(SystemExit):
            setup.install_rqbit_ca(d, "1024", "100")
        self.assertIn("boom", out.getvalue())


class TestTvSetup(unittest.TestCase):
    def test_data_dirs_include_downloads_and_shows(self):
        import setup
        with tempfile.TemporaryDirectory() as data_dir:
            setup.DirectorySetup(data_dir, os.path.join(data_dir, "config")).create()
            for d in ("movies", "downloads", "shows"):
                self.assertTrue(os.path.isdir(os.path.join(data_dir, d)), d)

    def test_plan_tv_indexers_adds_missing_only(self):
        from setup import plan_tv_indexers, TV_INDEXERS
        self.assertEqual(TV_INDEXERS, ["showRSS", "LimeTorrents", "TorrentDownload", "Nyaa.si"])
        self.assertEqual(plan_tv_indexers([]), TV_INDEXERS)
        self.assertEqual(plan_tv_indexers([{"name": "YTS"}, {"name": "showRSS"}]),
                         ["LimeTorrents", "TorrentDownload", "Nyaa.si"])
        self.assertEqual(plan_tv_indexers([{"name": n} for n in TV_INDEXERS]), [])

    def test_chown_tree_recursive_skips_symlinks_and_correct_owners(self):
        import setup
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "a", "b"))
            open(os.path.join(d, "a", "f"), "w").close()
            os.symlink(d, os.path.join(d, "a", "link"))
            me = os.lstat(d)
            with mock.patch("os.lchown") as lchown:
                setup.chown_tree(d, me.st_uid + 1, me.st_gid)
                changed = {c[0][0] for c in lchown.call_args_list}
                self.assertEqual(changed, {d, os.path.join(d, "a"), os.path.join(d, "a", "b"),
                                           os.path.join(d, "a", "f")})
                lchown.reset_mock()
                setup.chown_tree(d, str(me.st_uid), str(me.st_gid))  # already correct
                lchown.assert_not_called()

    def test_chown_tree_missing_path_is_noop(self):
        import setup
        setup.chown_tree("/nonexistent-cinema-path", 1, 1)

    def test_verify_hardlinks_inode_compare(self):
        import setup
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "downloads"))
            os.makedirs(os.path.join(d, "shows"))
            src = os.path.join(d, "downloads", ".cinema-link-test")
            dst = os.path.join(d, "shows", ".cinema-link-test")

            def runner(user):
                def fake(cmd, **kw):
                    if "inspect" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, stdout=user + "\n", stderr="")
                    self.assertIn("1024:100", cmd)
                    self.assertEqual(cmd[-3:], ["ln", "/data/downloads/.cinema-link-test",
                                                "/data/shows/.cinema-link-test"])
                    os.link(src, dst)  # what the container's ln does
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return fake

            with mock.patch("os.chown"), mock.patch("setup.docker_bin", return_value="/bin/docker"):
                with mock.patch("subprocess.run", side_effect=runner("1024:100")):
                    self.assertIsNone(setup.verify_hardlinks(d, "1024", "100"))
                self.assertFalse(os.path.exists(src))
                self.assertFalse(os.path.exists(dst))
                with mock.patch("subprocess.run", side_effect=runner("")):
                    reason = setup.verify_hardlinks(d, "1024", "100")  # wrong rqbit user
                    self.assertIn("rqbit runs as ''", reason)
                    self.assertIn("1024:100", reason)
                self.assertFalse(os.path.exists(src))
                self.assertFalse(os.path.exists(dst))

    def test_verify_hardlinks_different_inodes_fails_and_cleans_up(self):
        import setup
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "downloads"))
            os.makedirs(os.path.join(d, "shows"))
            src = os.path.join(d, "downloads", ".cinema-link-test")
            dst = os.path.join(d, "shows", ".cinema-link-test")

            def fake(cmd, **kw):
                if "inspect" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout="1024:100\n", stderr="")
                shutil_copy(src, dst)  # a copy, not a hardlink
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            import shutil as _sh
            shutil_copy = _sh.copyfile
            with mock.patch("os.chown"), mock.patch("subprocess.run", side_effect=fake):
                self.assertIn("different inode", setup.verify_hardlinks(d, "1024", "100"))
            self.assertFalse(os.path.exists(src))
            self.assertFalse(os.path.exists(dst))

    def test_verify_hardlinks_ln_failure_cleans_up(self):
        import setup
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "downloads"))
            os.makedirs(os.path.join(d, "shows"))

            def fake(cmd, **kw):
                return subprocess.CompletedProcess(cmd, 1, stdout="1024:100\n", stderr="denied")

            with mock.patch("os.chown"), mock.patch("subprocess.run", side_effect=fake):
                reason = setup.verify_hardlinks(d, "1024", "100")
            self.assertIn("ln failed", reason)
            self.assertIn("denied", reason)
            self.assertEqual(os.listdir(os.path.join(d, "downloads")), [])

    def test_compose_pins_sonarr_and_rqbit_user(self):
        path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
        with open(path) as f:
            text = f.read()
        self.assertIn("image: lscr.io/linuxserver/sonarr:develop-5.0.0.2941-ls209", text)
        self.assertIn('user: "${PUID}:${PGID}"', text)
        self.assertIn('command: ["server", "start", "/data/downloads"]', text)
        self.assertIn("- ${CONFIG_PATH}/sonarr:/sonarr-config:ro", text)
        self.assertIn("- SONARR_API_KEY=${SONARR_API_KEY:-}", text)
        self.assertIn("- DOTNET_GCConserveMemory=7", text)

    def test_compose_caps_sonarr_memory(self):
        path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
        with open(path) as f:
            text = f.read()
        start = text.index("\n  sonarr:\n")
        end = text.index("\n  flaresolverr:\n")
        sonarr = text[start:end]
        self.assertIn("- DOTNET_GCHeapHardLimit=0x10000000", sonarr)
        self.assertIn("\n    mem_limit: 400m\n", sonarr)


def make_sonarr(state=None):
    import setup
    cfg = setup.SonarrConfigurator.__new__(setup.SonarrConfigurator)
    cfg.api_key = "sonarr-secret"
    cfg.puid, cfg.pgid = "1024", "100"
    state = state if state is not None else {}
    state.setdefault("rootfolder", [])
    state.setdefault("downloadclient", [])
    state.setdefault("customformat", [])
    state.setdefault("qualityprofile", [])
    state.setdefault("mediamanagement", {"id": 1, "copyUsingHardlinks": False})
    state.setdefault("downloadclientconfig", {"id": 1, "enableCompletedDownloadHandling": False})
    calls = []
    qualities = ["HDTV-720p", "HDTV-1080p", "WEBRip-720p", "WEBRip-1080p", "WEBDL-720p",
                 "WEBDL-1080p", "Bluray-720p", "Bluray-1080p", "DVD", "SDTV"]

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if (method, path) == ("GET", "/system/status"):
            return {}
        if method == "GET" and path in ("/rootfolder", "/downloadclient", "/customformat",
                                        "/qualityprofile"):
            return state[path[1:]]
        if (method, path) == ("GET", "/config/mediamanagement"):
            return state["mediamanagement"]
        if (method, path) == ("PUT", "/config/mediamanagement/1"):
            state["mediamanagement"] = body
            return body
        if (method, path) == ("GET", "/config/downloadclient"):
            return state["downloadclientconfig"]
        if (method, path) == ("PUT", "/config/downloadclient/1"):
            state["downloadclientconfig"] = body
            return body
        if (method, path) == ("GET", "/downloadclient/schema"):
            return [{"implementation": "Transmission", "fields": []},
                    {"implementation": "RQBit", "configContract": "RQbitSettings",
                     "protocol": "torrent", "fields": [
                         {"name": "host", "value": "localhost"}, {"name": "port", "value": 3030},
                         {"name": "useSsl", "value": False}, {"name": "urlBase", "value": "/"}]}]
        if (method, path) == ("GET", "/customformat/schema"):
            return [{"implementation": "ReleaseTitleSpecification", "name": "ReleaseTitle",
                     "fields": [{"name": "value", "value": ""}]}]
        if (method, path) == ("GET", "/qualityprofile/schema"):
            return {"name": "", "items": [{"quality": {"id": i + 1, "name": n}, "items": [],
                                           "allowed": False} for i, n in enumerate(qualities)],
                    "formatItems": [{"format": f["id"], "name": f["name"], "score": 0}
                                    for f in state["customformat"]]}
        if method == "POST" and path == "/rootfolder":
            state["rootfolder"].append(body)
            return body
        if method == "POST" and path == "/downloadclient":
            state["downloadclient"].append(body)
            return body
        if method == "POST" and path == "/customformat":
            body = dict(body, id=7)
            state["customformat"].append(body)
            return body
        if method == "POST" and path == "/qualityprofile":
            state["qualityprofile"].append(body)
            return body
        raise AssertionError(f"unexpected {method} {path}")

    cfg._request = fake
    return cfg, calls, state


class TestSonarrConfigurator(unittest.TestCase):
    def test_reads_api_key_and_url(self):
        import setup
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sonarr"))
            with open(os.path.join(d, "sonarr", "config.xml"), "w") as f:
                f.write("<Config><ApiKey>sk</ApiKey></Config>")
            cfg = setup.SonarrConfigurator(d, "1024", "100")
            self.assertEqual(cfg.api_key, "sk")
            self.assertEqual(setup.SonarrConfigurator.BASE_URL, "http://localhost:8989")

    def test_sonarr_configure_adds_missing_pieces(self):
        cfg, calls, state = make_sonarr()
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.configure()
        self.assertEqual(state["rootfolder"], [{"path": "/data/shows"}])

        (client,) = state["downloadclient"]
        self.assertEqual(client["implementation"], "RQBit")
        self.assertEqual(client["configContract"], "RQbitSettings")
        self.assertIs(client["enable"], True)
        self.assertIs(client["removeCompletedDownloads"], False)
        self.assertIs(client["removeFailedDownloads"], True)
        fields = {f["name"]: f["value"] for f in client["fields"]}
        self.assertEqual((fields["host"], fields["port"], fields["urlBase"], fields["useSsl"]),
                         ("rqbit", 3030, "/", False))

        self.assertIs(state["mediamanagement"]["copyUsingHardlinks"], True)
        self.assertEqual(state["mediamanagement"]["downloadPropersAndRepacks"], "doNotUpgrade")
        self.assertIs(state["downloadclientconfig"]["enableCompletedDownloadHandling"], True)

        (cf,) = state["customformat"]
        self.assertEqual(cf["name"], "French only")
        specs = cf["specifications"]
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0]["fields"][0]["value"], r"\b(TRUE)?FRENCH\b|\bVF[FQI]?\b")
        self.assertIs(specs[0]["negate"], False)
        self.assertEqual(specs[1]["fields"][0]["value"], r"\bMULTI\b")
        self.assertIs(specs[1]["negate"], True)
        for sp in specs:
            self.assertEqual(sp["implementation"], "ReleaseTitleSpecification")
            self.assertIs(sp["required"], True)

        profiles = {p["name"]: p for p in state["qualityprofile"]}
        self.assertEqual(set(profiles), {"Cinema", "Cinema FR"})
        for p in profiles.values():
            allowed = {i["quality"]["name"] for i in p["items"] if i["allowed"]}
            self.assertEqual(allowed, {"HDTV-720p", "HDTV-1080p", "WEBRip-720p", "WEBRip-1080p",
                                       "WEBDL-720p", "WEBDL-1080p", "Bluray-720p",
                                       "Bluray-1080p"})
            self.assertEqual(p["cutoff"], 6)  # WEBDL-1080p
            self.assertIs(p["upgradeAllowed"], False)
            self.assertEqual(p["minFormatScore"], 0)
        self.assertEqual(profiles["Cinema"]["formatItems"][0]["score"], -10000)
        self.assertEqual(profiles["Cinema"]["formatItems"][0]["format"], 7)
        self.assertEqual(profiles["Cinema FR"]["formatItems"][0]["score"], 0)

    def test_sonarr_configure_idempotent(self):
        cfg, calls, state = make_sonarr()
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.configure()
        del calls[:]
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.configure()
        self.assertEqual([c for c in calls if c[0] in ("POST", "PUT", "DELETE")], [])

    def test_sonarr_stops_proper_and_repack_upgrades(self):
        cfg, calls, state = make_sonarr({"mediamanagement": {
            "id": 1, "copyUsingHardlinks": True, "downloadPropersAndRepacks": "preferAndUpgrade",
            "recycleBin": "/keep"}})
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.configure()
        puts = [c for c in calls if c[:2] == ("PUT", "/config/mediamanagement/1")]
        self.assertEqual(len(puts), 1)
        self.assertEqual(state["mediamanagement"], {
            "id": 1, "copyUsingHardlinks": True, "downloadPropersAndRepacks": "doNotUpgrade",
            "recycleBin": "/keep"})

    def test_sonarr_quality_groups_cutoff_uses_group_id(self):
        cfg, calls, state = make_sonarr()
        real = cfg._request

        def grouped(method, path, body=None):
            result = real(method, path, body)
            if (method, path) == ("GET", "/qualityprofile/schema"):
                items = result["items"]
                web = [i for i in items if i["quality"]["name"] in ("WEBDL-1080p", "WEBRip-1080p")]
                rest = [i for i in items if i not in web]
                rest.append({"quality": None, "id": 1001, "name": "WEB 1080p", "allowed": False,
                             "items": web})
                result = dict(result, items=rest)
            return result

        cfg._request = grouped
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.configure()
        profile = state["qualityprofile"][0]
        self.assertEqual(profile["cutoff"], 1001)
        group = [i for i in profile["items"] if i.get("id") == 1001][0]
        self.assertIs(group["allowed"], True)

    def test_sonarr_wait_until_ready_retries(self):
        import setup
        cfg = setup.SonarrConfigurator.__new__(setup.SonarrConfigurator)
        attempts = []

        def fake(method, path, body=None):
            attempts.append(path)
            if len(attempts) < 3:
                raise OSError("down")

        cfg._request = fake
        cfg.wait_until_ready(timeout=5, interval=0)
        self.assertEqual(len(attempts), 3)

    def test_prowlarr_adds_sonarr_application_once(self):
        import setup
        cfg = setup.ProwlarrConfigurator.__new__(setup.ProwlarrConfigurator)
        cfg.api_key = "k"
        apps = [{"id": 1, "name": "Other", "implementation": "Lidarr"}]
        calls = []

        def fake(method, path, body=None):
            calls.append((method, path, body))
            if (method, path) == ("GET", "/applications"):
                return apps
            if (method, path) == ("GET", "/applications/schema"):
                return [{"implementation": "Sonarr", "configContract": "SonarrSettings",
                         "fields": [{"name": n, "value": None} for n in
                                    ("prowlarrUrl", "baseUrl", "apiKey", "syncCategories")]}]
            if (method, path) == ("POST", "/applications?forceSave=true"):
                apps.append(body)
                return body

        cfg._request = fake
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cfg.add_sonarr_application("sonarr-secret")
            cfg.add_sonarr_application("sonarr-secret")
        posts = [c for c in calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        body = posts[0][2]
        self.assertEqual(body["name"], "Sonarr")
        self.assertEqual(body["implementation"], "Sonarr")
        fields = {f["name"]: f["value"] for f in body["fields"]}
        self.assertEqual(fields["prowlarrUrl"], "http://prowlarr:9696")
        self.assertEqual(fields["baseUrl"], "http://sonarr:8989")
        self.assertEqual(fields["apiKey"], "sonarr-secret")
        self.assertEqual(fields["syncCategories"],
                         [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5070])
        self.assertNotIn("sonarr-secret", out.getvalue())

    def test_prowlarr_adds_tv_indexers_missing_only(self):
        import setup
        cfg = setup.ProwlarrConfigurator.__new__(setup.ProwlarrConfigurator)
        cfg.api_key = "k"
        calls = []

        def fake(method, path, body=None):
            calls.append((method, path, body))
            if (method, path) == ("GET", "/indexer"):
                return [{"name": "showRSS"}]
            if (method, path) == ("GET", "/indexer/schema"):
                return [{"name": n, "implementation": n} for n in setup.TV_INDEXERS]

        cfg._request = fake
        with contextlib.redirect_stdout(io.StringIO()):
            cfg.add_tv_indexers()
        posted = [c[2]["name"] for c in calls if c[0] == "POST"]
        self.assertEqual(posted, ["LimeTorrents", "TorrentDownload", "Nyaa.si"])


class TestMainOrder(unittest.TestCase):
    def test_hardlink_failure_exits_nonzero_with_cause_after_stack(self):
        import setup
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            env = os.path.join(d, ".env")
            write_env(env, {"DATA_PATH": d, "CONFIG_PATH": os.path.join(d, "c")})
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch.object(setup, "chown_tree"), \
                        mock.patch.object(setup, "migrate_prowlarr_config", return_value=False), \
                        mock.patch.object(setup, "StackLauncher"), \
                        mock.patch.object(setup, "ProwlarrConfigurator") as pc, \
                        mock.patch.object(setup, "SonarrConfigurator") as sc, \
                        mock.patch.object(setup, "verify_hardlinks",
                                          return_value="rqbit runs as '', expected PUID:PGID (1024:100)"), \
                        mock.patch.object(setup, "install_rqbit_ca"), \
                        mock.patch.object(setup, "detect_ip", return_value="1.2.3.4"), \
                        mock.patch.object(setup, "tailscale_status", return_value=None), \
                        mock.patch("time.sleep"), \
                        contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                    setup.main()
            finally:
                os.chdir(cwd)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("rqbit runs as '', expected PUID:PGID (1024:100)", out.getvalue())
        self.assertNotIn("sysctl", out.getvalue())
        sc.return_value.configure.assert_called_once()
        pc.return_value.add_sonarr_application.assert_called_once()
        pc.return_value.add_tv_indexers.assert_called_once()

    def test_data_dirs_chowned_again_after_the_stack_starts(self):
        # The old root rqbit may still write until `docker compose up` replaces it.
        import setup
        calls = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            env = os.path.join(d, ".env")
            cfg = os.path.join(d, "c")
            write_env(env, {"DATA_PATH": d, "CONFIG_PATH": cfg})
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch.object(setup, "chown_tree", calls.chown), \
                        mock.patch.object(setup, "migrate_prowlarr_config", return_value=False), \
                        mock.patch.object(setup, "StackLauncher", calls.launcher), \
                        mock.patch.object(setup, "ProwlarrConfigurator"), \
                        mock.patch.object(setup, "SonarrConfigurator"), \
                        mock.patch.object(setup, "verify_hardlinks", return_value=None), \
                        mock.patch.object(setup, "install_rqbit_ca", calls.ca), \
                        mock.patch.object(setup, "detect_ip", return_value="1.2.3.4"), \
                        mock.patch.object(setup, "tailscale_status", return_value=None), \
                        mock.patch("time.sleep"), \
                        contextlib.redirect_stdout(io.StringIO()):
                    setup.main()
            finally:
                os.chdir(cwd)
        names = [name for name, _, _ in calls.mock_calls]
        self.assertLess(names.index("ca"), names.index("launcher().start"))
        order = [(name, args) for name, args, _ in calls.mock_calls
                 if name in ("chown", "launcher().start", "launcher().wait_for_healthy")]
        data = [("chown", (os.path.join(d, sub), "1024", "100"))
                for sub in ("movies", "downloads", "shows")]
        rqbit = ("chown", (os.path.join(cfg, "rqbit"), "1024", "100"))
        sonarr = ("chown", (os.path.join(cfg, "sonarr"), "1024", "100"))
        start = order.index(("launcher().start", ()))
        self.assertEqual(order[:start], data + [rqbit, sonarr])
        self.assertEqual(order[start + 1:start + 5], data + [rqbit])
        self.assertEqual(order[start + 5][0], "launcher().wait_for_healthy")


NEEDS_LOGIN = {"BackendState": "NeedsLogin", "AuthURL": "https://login.tailscale.com/a/abc123",
               "Self": None, "TailscaleIPs": None}
RUNNING = {"BackendState": "Running", "AuthURL": "",
           "Self": {"DNSName": "cinema.tail1234.ts.net."},
           "TailscaleIPs": ["100.64.0.7", "fd7a::7"]}


def run_main(extra_env=None, status=None, status_side_effect=None):
    """Run setup.main() with everything mocked; returns (stdout, sleep mock)."""
    import setup
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        write_env(os.path.join(d, ".env"),
                  dict({"DATA_PATH": d, "CONFIG_PATH": os.path.join(d, "c")}, **(extra_env or {})))
        cwd = os.getcwd()
        os.chdir(d)
        try:
            with mock.patch.object(setup, "chown_tree"), \
                    mock.patch.object(setup, "migrate_prowlarr_config", return_value=False), \
                    mock.patch.object(setup, "StackLauncher"), \
                    mock.patch.object(setup, "ProwlarrConfigurator"), \
                    mock.patch.object(setup, "SonarrConfigurator"), \
                    mock.patch.object(setup, "verify_hardlinks", return_value=None), \
                    mock.patch.object(setup, "install_rqbit_ca"), \
                    mock.patch.object(setup, "detect_ip", return_value="1.2.3.4"), \
                    mock.patch.object(setup, "tailscale_status", return_value=status,
                                      side_effect=status_side_effect), \
                    mock.patch("time.sleep") as sleep, \
                    contextlib.redirect_stdout(out):
                setup.main()
        finally:
            os.chdir(cwd)
    return out.getvalue(), sleep


class TestTailscale(unittest.TestCase):
    def test_compose_tailscale_pinned_userspace(self):
        path = os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
        with open(path) as f:
            text = f.read()
        block = text[text.index("\n  tailscale:\n"):]
        block = block[:block.index("\nnetworks:")] if "\nnetworks:" in block else block
        for line in ("image: tailscale/tailscale:v1.104.7", "container_name: tailscale",
                     "network_mode: host", "- TS_HOSTNAME=cinema", "- TS_USERSPACE=true",
                     "- TS_STATE_DIR=/var/lib/tailscale", "- TS_AUTHKEY=${TS_AUTHKEY:-}",
                     "- TS_ACCEPT_DNS=false", "- ${CONFIG_PATH}/tailscale:/var/lib/tailscale",
                     "restart: unless-stopped"):
            self.assertIn(line, block)
        self.assertNotIn("TS_SERVE", block)
        self.assertNotIn("funnel", block.lower())
        import setup
        self.assertIn("tailscale", setup.CONFIG_SERVICES)

    def test_tailscale_login_url_and_address(self):
        import setup
        self.assertEqual(setup.tailscale_login_url(NEEDS_LOGIN),
                         "https://login.tailscale.com/a/abc123")
        self.assertIsNone(setup.tailscale_login_url(RUNNING))
        self.assertIsNone(setup.tailscale_login_url(None))
        self.assertEqual(setup.tailscale_address(RUNNING), "cinema.tail1234.ts.net")
        self.assertIsNone(setup.tailscale_address(NEEDS_LOGIN))
        ip_only = {"BackendState": "Running", "Self": {"DNSName": ""},
                   "TailscaleIPs": ["100.64.0.7"]}
        self.assertEqual(setup.tailscale_address(ip_only), "100.64.0.7")
        self.assertIsNone(setup.tailscale_address(None))

    def test_tailscale_status_parses_and_fails_to_none(self):
        import setup
        ok = subprocess.CompletedProcess([], 0, stdout='{"BackendState": "Running"}', stderr="")
        with mock.patch("subprocess.run", return_value=ok) as run:
            self.assertEqual(setup.tailscale_status(), {"BackendState": "Running"})
        self.assertEqual(run.call_args[0][0][0], "sudo")
        self.assertEqual(run.call_args[0][0][2:],
                         ["exec", "tailscale", "tailscale", "status", "--json"])
        bad = subprocess.CompletedProcess([], 1, stdout="", stderr="no such container")
        with mock.patch("subprocess.run", return_value=bad):
            self.assertIsNone(setup.tailscale_status())
        junk = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        with mock.patch("subprocess.run", return_value=junk):
            self.assertIsNone(setup.tailscale_status())
        with mock.patch("subprocess.run", side_effect=OSError("no sudo")):
            self.assertIsNone(setup.tailscale_status())

    def test_tailscale_failure_warns_not_fails(self):
        out, _ = run_main(status=None)
        self.assertIn("WARNING", out)
        self.assertIn("Tailscale", out)
        self.assertIn("Setup complete!", out)
        out, _ = run_main(status_side_effect=ValueError("boom"))
        self.assertIn("WARNING", out)
        self.assertIn("Setup complete!", out)

    def test_login_url_printed_once_and_rerun_hint_when_still_waiting(self):
        out, sleep = run_main(status=NEEDS_LOGIN)
        self.assertEqual(out.count(NEEDS_LOGIN["AuthURL"]), 1)
        self.assertIn("re-run", out)
        self.assertLessEqual(sleep.call_count * 3, 120 + 3 + 10 + 3)  # bounded wait
        self.assertIn("Setup complete!", out)

    def test_running_prints_address_and_policy(self):
        out, _ = run_main(status=RUNNING)
        self.assertIn("cinema.tail1234.ts.net", out)
        self.assertIn("tag:cinema", out)
        self.assertNotIn("re-run", out)

    def test_access_policy_mentions_only_8888(self):
        import re
        import setup
        snippet = setup.ACCESS_POLICY_SNIPPET
        self.assertIn('"grants"', snippet)
        self.assertIn("group:family", snippet)
        self.assertIn("autogroup:shared", snippet)
        self.assertIn("tag:cinema", snippet)
        self.assertIn("tagOwners", snippet)
        self.assertEqual(re.findall(r"(?:tcp|udp):[\w*-]+", snippet), ["tcp:8888"])
        self.assertIn("allow-all", snippet)

    def test_authkey_never_printed(self):
        out, _ = run_main(extra_env={"TS_AUTHKEY": "tskey-auth-SECRET123"}, status=NEEDS_LOGIN)
        self.assertNotIn("tskey-auth-SECRET123", out)
        self.assertNotIn("tskey", out)


if __name__ == "__main__":
    unittest.main()
