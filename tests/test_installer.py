"""安装器的步骤表必须可单测：不真装东西也能验证顺序、失败即停、dry-run 不动盘。

这个文件里有一条硬规矩：autouse 的 _no_real_subprocess 把 installer 模块里的 subprocess
换成一炸就报的桩，所以任何步骤想绕过注入的 runner 直接开进程，测试立刻红 —— 单测阶段
绝不会真的建 venv、装依赖、下 Chromium。所有真跑路径都由 FakeRunner 记账，凭的是
"它记下本来要执行的那条命令"，而不是"我们相信实现没动手"。
"""

import hashlib
import os
import subprocess
import sys

import pytest

import installer as ins

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTECTED = ["data.json", "orders.db", "catalog.json"]


class _ForbiddenSubprocess:
    """替 installer 拿走的 subprocess：任何真实进程调用都直接判失败。"""

    @staticmethod
    def run(*a, **kw):
        raise AssertionError("installer 在单测里真的开了进程：%r" % (a,))


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    monkeypatch.setattr(ins, "subprocess", _ForbiddenSubprocess)


class FakeRunner:
    """假执行器：只记账，可按脚本故意让某一步失败（返回非 0 退出码）。

    它有一个必须的行为细节：看到 `-m venv <dir>` 时真把 <dir>/Scripts/python.exe
    这个空壳建出来。这样"第二次跑自动跳过建 venv"才是被观察到的事实，而不是测试
    自己把 ctx 编成一个已存在的目录来自证。
    """

    def __init__(self, fail_when=None, rc=1):
        self.calls = []
        self.fail_when = fail_when or ()
        self.rc = rc

    def __call__(self, argv, cwd=None):
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        if any(frag in joined for frag in self.fail_when):
            return self.rc
        if argv[1:3] == ["-m", "venv"]:
            scripts = os.path.join(argv[3], "Scripts")
            os.makedirs(scripts, exist_ok=True)
            open(os.path.join(scripts, "python.exe"), "wb").close()
        return 0


def ctx_for(tmp_path, runner=None, **extra):
    ctx = {"runner": runner or FakeRunner(), "venv_dir": str(tmp_path / ".venv")}
    ctx.update(extra)
    return ctx


def _sha(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _names(report):
    return [(r["name"], r["status"]) for r in report]


def _venv_creates(calls):
    return [c for c in calls if c[1:3] == ["-m", "venv"]]


# ── 计划里那五条：步骤表的语义 ─────────────────────────────────

class Recorder:
    def __init__(self):
        self.calls = []

    def step(self, name):
        def run(ctx):
            self.calls.append(name)
            return "做完了 %s" % name
        return run


def test_steps_run_in_declared_order(tmp_path):
    rec = Recorder()
    steps = [ins.Step(n, rec.step(n)) for n in ("python", "venv", "deps", "browser", "check")]
    report = ins.run_steps(steps, {"tmp": tmp_path})
    assert [c for c in rec.calls] == ["python", "venv", "deps", "browser", "check"]
    assert all(r["status"] == "ok" for r in report)


def test_a_failing_step_stops_the_rest():
    rec = Recorder()

    def broken(ctx):
        raise RuntimeError("pip 炸了")
    steps = [ins.Step("a", rec.step("a")), ins.Step("b", broken), ins.Step("c", rec.step("c"))]
    report = ins.run_steps(steps, {})
    assert [r["name"] for r in report] == ["a", "b"]
    assert report[1]["status"] == "fail"
    assert "pip 炸了" in report[1]["detail"]
    assert rec.calls == ["a"]                       # c 没被执行


def test_skip_is_reported_not_ok():
    """已装过的步骤必须报 skip，不能冒充 ok —— 否则第二次跑就看不出它做了什么。"""
    def skipped(ctx):
        return "%s：.venv 已存在" % ins.SKIP
    report = ins.run_steps([ins.Step("venv", skipped)], {})
    assert report[0]["status"] == "skip"
    assert ".venv 已存在" in report[0]["detail"]


def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(ins, "run_steps", lambda steps, ctx: ran.append(len(steps)))
    assert ins.main(["--dry-run"]) == 0
    assert ran == []                                # dry-run 不执行任何步骤
    for name in ("python", "venv", "deps", "browser", "check"):
        assert name in [s.name for s in ins.build_steps(dry_run=True)]


def test_exit_code_one_when_a_step_failed(tmp_path, monkeypatch):
    def broken(ctx):
        raise RuntimeError("网络不通")
    monkeypatch.setattr(ins, "build_steps", lambda dry_run=False: [ins.Step("deps", broken)])
    assert ins.main([]) == 1


def test_dry_run_asks_the_runner_for_nothing(tmp_path):
    """dry-run 连一条命令都不该交给执行器 —— 打印步骤表不等于动手装。"""
    runner = FakeRunner()
    ctx = ctx_for(tmp_path, runner)
    assert ins.main(["--dry-run"], ctx) == 0
    assert runner.calls == []
    assert not os.path.exists(ctx["venv_dir"])


# ── 五条真步骤：命令形状与每条失败路径 ─────────────────────────

def test_real_steps_issue_exactly_the_documented_commands(tmp_path):
    runner = FakeRunner()
    ctx = ctx_for(tmp_path, runner)
    report = ins.run_steps(ins.build_steps(), ctx)
    venvpy = ins.venv_python(ctx["venv_dir"])
    assert _names(report) == [("python", "ok"), ("venv", "ok"), ("deps", "ok"),
                              ("browser", "ok"), ("check", "ok")]
    assert runner.calls == [
        [sys.executable, "-m", "venv", ctx["venv_dir"]],
        [venvpy, "-m", "pip", "install", "-r", ins.REQUIREMENTS],
        [venvpy, "-m", "playwright", "install", "chromium"],
        [venvpy, ins.SELFCHECK_SCRIPT],
    ]


def test_heavy_steps_run_with_the_venv_python_not_the_system_one(tmp_path):
    """不动他的系统 Python 是这套安装器的立身之本：装包/下内核/自检必须用 .venv 那个解释器。"""
    runner = FakeRunner()
    ctx = ctx_for(tmp_path, runner)
    ins.run_steps(ins.build_steps(), ctx)
    venvpy = ins.venv_python(ctx["venv_dir"])
    heavy = [c for c in runner.calls
             if any(k in " ".join(c) for k in ("pip install", "playwright install", "selfcheck.py"))]
    assert len(heavy) == 3, heavy
    assert all(c[0] == venvpy for c in heavy), heavy
    assert ctx["venv_dir"] != ins.VENV_DIR          # 测试用的 venv 不在仓库里


def test_python_step_fails_on_too_old_interpreter(tmp_path):
    """版本闸门必须能红：他机器是 3.13，但别人拿 3.8 来装时得拦下来。"""
    report = ins.run_steps(ins.build_steps(), ctx_for(tmp_path, version_info=(3, 9)))
    assert report[0]["status"] == "fail"
    assert "3.10" in report[0]["detail"]
    assert len(report) == 1                         # 后面四步没执行


def test_deps_failure_is_named_and_stops_the_sequence(tmp_path):
    runner = FakeRunner(fail_when=["pip install"])
    report = ins.run_steps(ins.build_steps(), ctx_for(tmp_path, runner))
    assert _names(report) == [("python", "ok"), ("venv", "ok"), ("deps", "fail")]
    assert "pip install" in report[-1]["detail"]


def test_browser_failure_points_at_the_network(tmp_path):
    """Chromium 是唯一要联网的大件，失败时必须指名网络/代理，别让人去翻代码。"""
    runner = FakeRunner(fail_when=["playwright install"])
    report = ins.run_steps(ins.build_steps(), ctx_for(tmp_path, runner))
    assert report[-1]["name"] == "browser" and report[-1]["status"] == "fail"
    assert "代理" in report[-1]["detail"] or "网络" in report[-1]["detail"]


def test_check_step_fails_when_selfcheck_reports_a_fail_row(tmp_path):
    """体检表退出码非 0 时安装器不许报成 ok —— 它必须把失败交给下一步的人。"""
    runner = FakeRunner(fail_when=["selfcheck.py"])
    report = ins.run_steps(ins.build_steps(), ctx_for(tmp_path, runner))
    assert report[-1]["name"] == "check" and report[-1]["status"] == "fail"
    assert "体检表" in report[-1]["detail"]


def test_check_step_hands_off_to_selfcheck_as_a_script(tmp_path):
    """最后一步只能交给 selfcheck.py，不在安装器里另起一套健康检查。"""
    runner = FakeRunner()
    ctx = ctx_for(tmp_path, runner)
    ins.run_steps(ins.build_steps(), ctx)
    assert runner.calls[-1][-1] == os.path.join(ins.BASE_DIR, "selfcheck.py")
    assert os.path.exists(runner.calls[-1][-1])
    assert not any("8765" in a or "/api/" in a for c in runner.calls for a in c)


def test_step_without_injected_runner_cannot_reach_a_real_process(tmp_path):
    """忘了注入 runner 时，唯一该被撞到的是禁区桩，而不是他机器上真的建出 .venv。"""
    ctx = {"venv_dir": str(tmp_path / "no-runner-venv")}
    report = ins.run_steps(ins.build_steps(), ctx)
    assert _names(report) == [("python", "ok"), ("venv", "fail")]
    assert "真的开了进程" in report[-1]["detail"]
    assert not os.path.exists(ctx["venv_dir"])


# ── 失败时不许说"都好了" ──────────────────────────────────────

def test_failed_run_leaves_a_legible_last_line_and_no_all_good(tmp_path, capsys):
    rc = ins.main([], ctx_for(tmp_path, FakeRunner(fail_when=["playwright install"])))
    out = capsys.readouterr().out
    assert rc == 1
    assert "browser" in out and "FAIL" in out
    assert "重新双击" in out                        # 告诉他下一步做什么
    assert "装好了" not in out                      # 失败之后不许报喜


def test_successful_run_points_at_the_venv_launcher(tmp_path, capsys):
    assert ins.main([], ctx_for(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "装好了" in out
    assert ".venv" in out and "launcher.py" in out  # 依赖只装在 .venv 里，指路也得指它


# ── 幂等：好机器上连跑两遍不许伤到数据 ─────────────────────────

def test_two_full_runs_are_idempotent_and_touch_no_data(tmp_path, capsys):
    """连跑两遍：venv 不重建、三个敏感文件字节不变、仓库里不会冒出 .venv。

    断的三件事：第二遍的 venv 行报 skip 而不是 ok；`-m venv` 总共只出现一次；
    data.json / orders.db / catalog.json 的 sha256 前后相同。
    """
    before = {f: _sha(os.path.join(ROOT, f)) for f in PROTECTED}
    ctx1 = ctx_for(tmp_path)
    ctx2 = ctx_for(tmp_path)                        # 同一个 venv_dir：第二遍看得见它已存在
    assert ctx1["venv_dir"] == ctx2["venv_dir"]

    assert _names(ins.run_steps(ins.build_steps(), ctx1)) == [
        ("python", "ok"), ("venv", "ok"), ("deps", "ok"), ("browser", "ok"), ("check", "ok")]
    assert _names(ins.run_steps(ins.build_steps(), ctx2)) == [
        ("python", "ok"), ("venv", "skip"), ("deps", "ok"), ("browser", "ok"), ("check", "ok")]
    assert ins.main([], ctx2) == 0                  # 已装好的机器上 main 也走到 0

    creates = _venv_creates(ctx1["runner"].calls + ctx2["runner"].calls)
    assert len(creates) == 1, creates
    venvpy = ins.venv_python(ctx2["venv_dir"])
    assert ctx2["runner"].calls[-3:] == [
        [venvpy, "-m", "pip", "install", "-r", ins.REQUIREMENTS],
        [venvpy, "-m", "playwright", "install", "chromium"],
        [venvpy, ins.SELFCHECK_SCRIPT],
    ]
    for f in PROTECTED:
        assert _sha(os.path.join(ROOT, f)) == before[f], "%s 被安装过程改过" % f
    assert not os.path.exists(ins.VENV_DIR)         # 仓库根下没长出 .venv


# ── setup.bat：双击入口必须是 cmd 能解析的形状 ─────────────────

def _bat_bytes():
    with open(os.path.join(ROOT, "setup.bat"), "rb") as f:
        return f.read()


def test_setup_bat_is_all_crlf_without_bom():
    raw = _bat_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "带 BOM 的 .bat 在 cmd 里第一行就崩"
    assert b"\r\r\n" not in raw
    lf_only = [i for i, b in enumerate(raw) if b == 0x0A and (i == 0 or raw[i - 1] != 0x0D)]
    assert not lf_only, "有 %d 处裸 LF，CRLF 不彻底" % len(lf_only)
    lines = raw.split(b"\r\n")
    assert lines[-1] == b"" and len(lines) > 10, "文件要以 CRLF 收尾"


def test_setup_bat_comments_and_commands_are_ascii_only():
    raw = _bat_bytes()
    non_ascii = [ln for ln in raw.split(b"\r\n") if any(b > 0x7F for b in ln)]
    assert non_ascii, "计划要求中文提示留在 .bat 里，一条都没有就是没照计划写"
    for ln in non_ascii:
        assert ln.startswith(b"echo "), "非 ASCII 出现在命令或注释里：%r" % ln
    assert raw.decode("utf-8"), "非 ASCII 那几行不是合法 UTF-8"


def test_setup_bat_declares_utf8_codepage_before_the_chinese_line():
    lines = _bat_bytes().decode("utf-8").split("\r\n")
    first_non_ascii = next(i for i, ln in enumerate(lines) if any(ord(c) > 127 for c in ln))
    codepage = next(i for i, ln in enumerate(lines) if ln.startswith("chcp 65001"))
    assert codepage < first_non_ascii


def test_setup_bat_only_uses_known_cmd_words():
    """cmd 没有 `end` 这个命令（计划稿末尾就是），跑完会多打一句"不是内部或外部命令"。"""
    allowed = {"@echo", "echo", "echo.", "setlocal", "endlocal", "cd", "chcp", "where",
               "py", "python", "if", "goto", "pause", "exit", "rem"}
    bad = []
    for ln in _bat_bytes().decode("utf-8").split("\r\n"):
        s = ln.strip()
        if not s or s[0] in ":()":               # 空行、标签、if 块的括号行都不算命令
            continue
        if s.split()[0].lower() not in allowed:
            bad.append(s)
    assert not bad, "cmd 不认识的行：%s" % (bad,)


def test_setup_bat_is_the_double_click_entry_and_nothing_retired():
    raw = _bat_bytes().decode("utf-8")
    assert "installer.py" in raw and "pause" in raw
    dead = [w for w in ("db_secret", "db_config", "pymysql", "mysql", "getpass")
            if w in raw.lower()]
    assert not dead, "setup.bat 还引用已退役的东西：%s" % (dead,)


def test_git_says_setup_bat_is_crlf():
    """本机写成 CRLF 不算数：问 git 哪条规则真的赢，而不是自己去读 .gitattributes 猜。"""
    out = subprocess.run(["git", "check-attr", "eol", "--", "setup.bat"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    assert out.endswith("eol: crlf"), "git 认为 setup.bat 的 eol 不是 crlf：%r" % (out,)


def test_a_fresh_checkout_of_setup_bat_is_crlf(tmp_path):
    """最硬的一条：真让 git 把 setup.bat 落到干净目录，看双击的那一份是不是 CRLF。"""
    rc = subprocess.run(["git", "checkout-index", "--force",
                         "--prefix=%s%s" % (tmp_path, os.sep), "--", "setup.bat"],
                        cwd=ROOT, capture_output=True, text=True)
    assert rc.returncode == 0, "checkout-index 没跑成：%s" % rc.stderr
    with open(os.path.join(str(tmp_path), "setup.bat"), "rb") as f:
        raw = f.read()
    assert raw, "签出来是空文件"
    assert raw.count(b"\n") == raw.count(b"\r\n"), "签出来的 .bat 不是纯 CRLF"
    assert raw == _bat_bytes(), "签出来的内容与工作区那份不一致"


def test_installer_source_has_no_mysql_password_prompt():
    """口令那一步随 MySQL 一起退役了：安装器里不该再有 getpass / 口令提示。"""
    with open(os.path.join(ROOT, "installer.py"), encoding="utf-8") as f:
        src = f.read()
    for frag in ("getpass", "password", "口令", "pymysql", "db_secret", "mysql"):
        assert frag not in src.lower(), "%s 还留在 installer.py 里" % frag


def test_requirements_still_drives_the_deps_step():
    """装的是 requirements.txt，而不是安装器里抄一份包名清单（抄的那份必然过期）。"""
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        req = f.read().lower()
    assert "playwright" in req and "pywebview" in req
    assert "pymysql" not in req
    assert ins.REQUIREMENTS == os.path.join(ins.BASE_DIR, "requirements.txt")


def test_setup_bat_and_installer_exist_side_by_side():
    """双击的那一份必须跟被拉起的那一份在同一个目录。"""
    assert os.path.exists(os.path.join(ROOT, "setup.bat"))
    assert os.path.exists(ins.__file__)
