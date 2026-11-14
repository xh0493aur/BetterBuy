"""公开仓库场景下的安全网测试。

对应三个新增能力：
1. ``betterbuy/access.py`` —— 可选访问口令（默认关闭，不改变本地行为）；
2. ``betterbuy/zero_cost.py`` 的公网演示模式 —— 陌生访客也不至于把额度烧光；
3. ``betterbuy/secretscan.py`` —— 上传前自检，抓明文密钥与不该跟踪的文件。
"""
import os
import tempfile
import unittest
from unittest import mock

TMPDIR = tempfile.mkdtemp(prefix="betterbuy_public_")
os.environ["BETTERBUY_DB"] = os.path.join(TMPDIR, "public.sqlite3")
os.environ["BETTERBUY_MIN_INTERVAL"] = "0"

from streamlit.testing.v1 import AppTest  # noqa: E402

from betterbuy.access import ENV_CODE, configured, mask_hint, verify  # noqa: E402
from betterbuy.secretscan import is_never_track, scan_text  # noqa: E402
from betterbuy.zero_cost import daily_limit, public_demo, total_limit  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


class AccessGateTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_CODE, None)
            self.assertFalse(configured())
            self.assertTrue(verify("anything"), "未启用时必须放行，否则本地跑不起来")
            self.assertEqual(mask_hint(), "未启用")

    def test_verify_matches_exactly(self):
        with mock.patch.dict(os.environ, {ENV_CODE: "s3cret-code"}):
            self.assertTrue(configured())
            self.assertTrue(verify("s3cret-code"))
            self.assertTrue(verify("  s3cret-code  "), "两端空白应被忽略")
            self.assertFalse(verify("s3cret-cod"))
            self.assertFalse(verify("S3CRET-CODE"))
            self.assertFalse(verify(""))
            self.assertFalse(verify(None))

    def test_hint_never_leaks_code(self):
        with mock.patch.dict(os.environ, {ENV_CODE: "s3cret-code"}):
            hint = mask_hint()
        self.assertNotIn("s3cret", hint)
        self.assertIn("已启用", hint)


class PublicDemoLimitTests(unittest.TestCase):
    def test_defaults_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("BETTERBUY_PUBLIC_DEMO", "BETTERBUY_DAILY_LIMIT_PDD",
                        "BETTERBUY_DAILY_LIMIT_TOTAL"):
                os.environ.pop(key, None)
            self.assertFalse(public_demo())
            self.assertEqual(daily_limit("pdd"), 200)
            self.assertEqual(total_limit(), 800)

    def test_public_demo_tightens_caps(self):
        with mock.patch.dict(os.environ, {"BETTERBUY_PUBLIC_DEMO": "1"}, clear=False):
            self.assertTrue(public_demo())
            self.assertEqual(daily_limit("pdd"), 20)
            self.assertEqual(daily_limit("jd"), 20)
            self.assertEqual(daily_limit("steam"), 100)
            self.assertEqual(total_limit(), 120)

    def test_explicit_override_wins_over_public_demo(self):
        env = {"BETTERBUY_PUBLIC_DEMO": "1", "BETTERBUY_DAILY_LIMIT_PDD": "5"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(daily_limit("pdd"), 5, "显式配置应优先于演示模式")
            self.assertEqual(total_limit(), 120, "未显式配置合计时仍用演示上限")

    def test_public_demo_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"BETTERBUY_PUBLIC_DEMO": "0"}, clear=False):
            self.assertFalse(public_demo())
            self.assertEqual(daily_limit("pdd"), 200)


class SecretScanTests(unittest.TestCase):
    # 样例字符串在运行时拼出来，避免本文件自己变成"含明文密钥的文件"
    SECRET_LINE = 'APP_SECRET = "' + "abcd" + "1234efgh5678" + '"\n'
    MODEL_KEY_LINE = 'client = OpenAI(api_key="sk-' + "proj-" + "abc123def456ghi789" + '")\n'
    WINDOWS_PATH_LINE = "DB = r'" + "D:" + "/work/data/x.db'\n"

    def test_plaintext_secret_is_error_and_masked(self):
        found = scan_text(self.SECRET_LINE, "config.py")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "error")
        self.assertEqual(found[0].line, 1)
        self.assertNotIn("abcd1234efgh5678", found[0].excerpt, "报告里不能再抄一遍密钥")
        self.assertIn("***", found[0].excerpt)

    def test_env_var_lookup_is_not_a_leak(self):
        text = (
            'key = os.environ.get("BETTERBUY_PDD_CLIENT_SECRET", "")\n'
            'secret = st.secrets.get("BETTERBUY_JD_APP_SECRET")\n'
        )
        self.assertEqual(scan_text(text, "betterbuy/openapi/pdd.py"), [])

    def test_placeholder_values_are_ignored(self):
        text = (
            'BETTERBUY_PDD_CLIENT_ID = ""\n'
            'BETTERBUY_JD_APP_KEY = "your_app_key_here"\n'
            'BETTERBUY_PDD_CLIENT_SECRET = "填这里"\n'
        )
        self.assertEqual(scan_text(text, ".streamlit/secrets.toml.example"), [])

    def test_never_track_files(self):
        for name in (".env", ".streamlit/secrets.toml", "data/betterbuy.sqlite3",
                     "deploy/id_rsa", "cert.pem"):
            self.assertTrue(is_never_track(name), name)
        for name in (".env.example", "secrets.toml.example", "app.py"):
            self.assertFalse(is_never_track(name), name)

    def test_windows_path_is_warn_only(self):
        found = scan_text(self.WINDOWS_PATH_LINE, "app.py")
        self.assertTrue(found)
        self.assertEqual(found[0].severity, "warn")

    def test_model_key_prefix_is_error(self):
        found = scan_text(self.MODEL_KEY_LINE, "x.py")
        self.assertTrue(any(f.severity == "error" for f in found))


class GateUiTests(unittest.TestCase):
    def test_gate_blocks_page_when_code_set(self):
        with mock.patch.dict(os.environ, {ENV_CODE: "let-me-in"}):
            at = AppTest.from_file(APP, default_timeout=90)
            at.run()
            self.assertFalse(at.exception)
            labels = [w.label for w in at.text_input]
            self.assertIn("访问口令", labels)
            self.assertFalse(any("帮我分析" == b.label for b in at.button),
                             "口令未通过时不应渲染主界面按钮")

    def test_gate_passes_with_correct_code(self):
        with mock.patch.dict(os.environ, {ENV_CODE: "let-me-in"}):
            at = AppTest.from_file(APP, default_timeout=90)
            at.run()
            at.text_input(key="bb_access_input").set_value("let-me-in")
            submit = next(b for b in at.button if b.label == "进入")
            submit.click().run()
            self.assertFalse(at.exception)
            self.assertTrue(at.session_state["bb_access_ok"])
            self.assertNotIn("访问口令", [w.label for w in at.text_input])

    def test_no_gate_without_code(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_CODE, None)
            at = AppTest.from_file(APP, default_timeout=90)
            at.run()
            self.assertFalse(at.exception)
            self.assertNotIn("访问口令", [w.label for w in at.text_input])


class SiteIdentityTests(unittest.TestCase):
    """正式地址只允许有一个来源：betterbuy/site.py。

    这些断言是"防手滑"用的：谁把网址改错、或者在文档里另写一个地址，测试会先红。
    """

    def test_official_url_is_the_cloud_app(self):
        from betterbuy.site import OFFICIAL_URL

        self.assertTrue(OFFICIAL_URL.startswith("https://"))
        self.assertIn("betterbuy-ewtcn8eechfdqdrr2zymch.streamlit.app", OFFICIAL_URL)
        self.assertTrue(OFFICIAL_URL.endswith("/"))

    def test_repo_and_main_file_unchanged(self):
        from betterbuy.site import GITHUB_REPO, GITHUB_URL, MAIN_FILE

        self.assertEqual(GITHUB_REPO, "xh0493aur/BetterBuy")
        self.assertEqual(GITHUB_URL, "https://github.com/xh0493aur/BetterBuy")
        self.assertEqual(MAIN_FILE, "app.py")

    def test_docs_quote_the_same_url(self):
        """部署文档里必须写同一个地址，不能各写各的。"""
        import pathlib

        from betterbuy.site import OFFICIAL_URL

        root = pathlib.Path(APP).parent
        deploy = (root / "DEPLOY.md").read_text(encoding="utf-8")
        self.assertIn(OFFICIAL_URL, deploy)
        # 除了这个地址，不应再出现别的 streamlit.app 域名
        others = {
            token.strip("()（）[]<>`\"'，。、")
            for token in deploy.replace("\n", " ").split(" ")
            if "streamlit.app" in token
        }
        others = {u for u in others if u.startswith("http") or u.startswith("www")}
        self.assertEqual(others - {OFFICIAL_URL}, set(), f"文档里出现了其他站点地址：{others}")


if __name__ == "__main__":
    unittest.main()
