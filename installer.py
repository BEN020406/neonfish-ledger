r"""一键装环境：venv + 依赖 + Playwright Chromium + 交给 selfcheck 出体检表。

给不折腾命令行的人用：双击同目录的 setup.bat 即可。三条规矩：

1. 只在本目录建 `.venv`，包全装进去，绝不写他系统里那个 Python；
2. 不碰数据库 —— 抓单落地的存储层（orders_db.py）本身不需要任何数据库服务，
   所以装机这一步不再要先起一个数据库、再往里配任何账号；
3. 每一步只报事实：ok / skip / FAIL，FAIL 必须带上"下一步你该做什么"，
   失败即停 —— 停住之后不会再打印任何"装好了"。

    python -X utf8 installer.py            # 真装
    python -X utf8 installer.py --dry-run  # 只打印要做哪几步，一条命令都不执行

步骤的副作用（建 venv、装包、下内核、跑自检）全部经 ctx["runner"] 交给外部执行，
单测里换成记账的假执行器就能验证顺序、失败即停与幂等，不必真装一遍。
"""

import argparse
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, ".venv")
REQUIREMENTS = os.path.join(BASE_DIR, "requirements.txt")
SELFCHECK_SCRIPT = os.path.join(BASE_DIR, "selfcheck.py")
MIN_PYTHON = (3, 10)
SKIP = "skip"


class Step:
    """一步 = 一个名字 + 一个可注入的动作；doc 只给 dry-run 的说明用。"""

    def __init__(self, name, run, doc=""):
        self.name = name
        self.run = run
        self.doc = doc


def venv_python(venv_dir=VENV_DIR):
    return os.path.join(venv_dir, "Scripts", "python.exe")


def local_runner(argv, cwd=None):
    """真执行一条命令，只回退出码。单测把整层换成假的。"""
    return subprocess.run(list(argv), cwd=cwd or BASE_DIR).returncode


def _runner(ctx):
    return ctx.get("runner") or local_runner


def _run(ctx, argv, cwd=None):
    try:
        return _runner(ctx)(list(argv), cwd=cwd)
    except OSError as exc:
        # 解释器路径不存在之类：别丢裸 traceback 给非技术的人
        raise RuntimeError("命令起不来：%s（%s）" % (" ".join(str(a) for a in argv), exc))


def _must(ctx, argv, next_step):
    rc = _run(ctx, argv)
    if rc != 0:
        raise RuntimeError("命令没成功（退出码 %s）：%s\n        下一步：%s"
                           % (rc, " ".join(str(a) for a in argv), next_step))
    return rc


def _venv_dir(ctx):
    return ctx.get("venv_dir") or VENV_DIR


def _python_ok(ctx):
    v = tuple(ctx.get("version_info") or sys.version_info[:2])
    if v < MIN_PYTHON:
        raise RuntimeError("需要 Python %d.%d 以上，当前只有 %d.%d。"
                           "去 python.org 装一个 3.10+ 再双击 setup.bat"
                           % (MIN_PYTHON[0], MIN_PYTHON[1], v[0], v[1]))
    return "Python %d.%d（要求 %d.%d 以上），只借用它建环境" % (
        v[0], v[1], MIN_PYTHON[0], MIN_PYTHON[1])


def _venv(ctx):
    venv_dir = _venv_dir(ctx)
    if os.path.exists(venv_python(venv_dir)):
        return "%s：%s 已存在，不重建（包本来就能重复装）" % (SKIP, venv_dir)
    _must(ctx, [sys.executable, "-m", "venv", venv_dir],
          "换到本目录写权限正常的地方再跑；这一步不联网")
    return "建好 %s（只往这里装包）" % venv_dir


def _deps(ctx):
    _must(ctx, [venv_python(_venv_dir(ctx)), "-m", "pip", "install", "-r", REQUIREMENTS],
          "多是网络或代理问题：确认能打开 pypi.org，公司网络先设好 HTTPS_PROXY 再重跑")
    return "requirements.txt 里的依赖装进 .venv 了"


def _browser(ctx):
    _must(ctx, [venv_python(_venv_dir(ctx)), "-m", "playwright", "install", "chromium"],
          "Chromium 是唯一要联网的大件（约 170MB），失败基本是网络/代理被断，"
          "换网络或设好 HTTPS_PROXY 后重新双击 setup.bat；只记账不抓单的话这步可以不管")
    return "Chromium 就位（抓单要用；只记账可不装）"


def _check(ctx):
    rc = _run(ctx, [venv_python(_venv_dir(ctx)), SELFCHECK_SCRIPT], cwd=BASE_DIR)
    if rc != 0:
        raise RuntimeError("自检有 FAIL 项，见上方体检表：按每一行写的下一步单独处理，"
                           "处理完重新双击 setup.bat")
    return "体检通过（skip 行不算失败，例如后端没起时的台账那一行）"


def build_steps(dry_run=False):
    """步骤表。dry_run 不裁剪步骤 —— 是否执行由 main 决定，这里只是留个口子给按模式排表。"""
    return [
        Step("python", _python_ok, "查解释器版本够不够（要求 3.10+），只借用它"),
        Step("venv", _venv, "在本目录建 .venv；已存在就跳过，不重建"),
        Step("deps", _deps, ".venv 里 pip install -r requirements.txt"),
        Step("browser", _browser, "playwright install chromium（唯一要联网的大件）"),
        Step("check", _check, "交给 selfcheck.py 出四行体检表"),
    ]


MARK = {"ok": "ok  ", "fail": "FAIL", "skip": "--  "}


def run_steps(steps, ctx):
    """按声明顺序执行；一遇 FAIL 就停，后面的步骤根本不被调用。"""
    report = []
    for step in steps:
        try:
            detail = step.run(ctx) or ""
            status = SKIP if str(detail).startswith(SKIP) else "ok"
        except Exception as exc:
            status, detail = "fail", str(exc)
        report.append({"name": step.name, "status": status, "detail": detail})
        print("[%s] %-8s %s" % (MARK[status], step.name, detail))
        if status == "fail":
            break
    return report


def main(argv=None, ctx=None):
    ap = argparse.ArgumentParser(description="霓虹鱼环境一键安装（不碰数据库，不碰系统 Python）")
    ap.add_argument("--dry-run", action="store_true", help="只打印要做哪几步，不执行任何命令")
    args = ap.parse_args(argv)
    ctx = ctx if ctx is not None else {}

    steps = build_steps(dry_run=args.dry_run)
    if args.dry_run:
        print("将依次执行：" + " → ".join(s.name for s in steps))
        for s in steps:
            print("  %-8s %s" % (s.name, s.doc))
        print("dry-run 只打印这张表，一条命令都没有执行。")
        return 0

    print("开始装环境：只在本目录建 .venv，不动你系统里的 Python，也不碰任何数据文件。")
    report = run_steps(steps, ctx)
    failed = [r for r in report if r["status"] == "fail"]
    if failed:
        print("\n停在这一步：%s。修好后重新双击 setup.bat 即可，已完成的步骤会跳过。"
              % failed[0]["name"])
        return 1
    if any(r["status"] == "skip" for r in report):
        print("（带 -- 的行是已经就绪、这次没重做的步骤，不是失败。）")
    print("\n装好了。日常使用：在本目录跑 .venv\\Scripts\\python.exe launcher.py 打开霓虹鱼面板。")
    print("        启动器会用同一个 Python 拉起台账与填入台，所以必须走 .venv 那一个；"
          "以后想复查就同一目录跑 .venv\\Scripts\\python.exe selfcheck.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
