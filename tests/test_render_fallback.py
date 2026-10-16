# -*- coding: utf-8 -*-
u"""渲染产物降级链回归测试 —— PNG 链 / SVG 链各一个。

★ 项目铁律 4：改渲染链要「两边各写一个测试」。本文件覆盖**产物层**：
  · 链 ① PNG 链 —— 装了 Pillow + pillow_lunasvg：出 PNG，同时保留 SVG
  · 链 ② SVG 链 —— 没装（用 `python -S` 屏蔽 site-packages，
                   等价于「用户只装了默认 Python，零第三方包」）：
                   只出 SVG、退出码仍为 0、给安装指引、不抛 ModuleNotFoundError
  · 单测 `shoot._pick_artifact()` 的三分支（PNG 优先 / SVG 兜底 / 都没有）

跑法：python tests/test_render_fallback.py
无第三方依赖（纯标准库 + assert）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_fail = []


def check(name, got, want):
    ok = (got == want)
    print(u'%s %s  got=%r want=%r' % (u'✅' if ok else u'❌', name, got, want))
    if not ok:
        _fail.append(name)


def _run_render(no_site, out_dir):
    u"""跑一次离线渲染，返回 (退出码, 日志)。

    no_site=True 时给解释器加 `-S` —— 跳过 site-packages，
    即「什么第三方包都没装」的等价环境。
    """
    env = dict(os.environ)
    env['WALKKCAL_OUT'] = out_dir
    for k in ('DISH_MODE', 'WALKKCAL_QUOTA', 'WALKKCAL_DATA',
              'WALKKCAL_FILTER', 'WALKKCAL_PAGE', 'WALKKCAL_DRINK'):
        env.pop(k, None)
    cmd = ([sys.executable] + (['-S'] if no_site else [])
           + [os.path.join(ROOT, 'walkkcal', 'render.py')])
    p = subprocess.run(cmd, cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=300)
    return p.returncode, (p.stdout or b'').decode('utf-8', 'ignore')


def _tmp():
    return tempfile.mkdtemp(prefix='wk_fallback_')


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


# ── 链 ①：装了 Pillow → 必须出 PNG，且 SVG 同时在 ──────────────
def test_png_chain():
    d = _tmp()
    try:
        code, log = _run_render(no_site=False, out_dir=d)
        png = os.path.join(d, 'skill_mobile.png')
        svg = os.path.join(d, 'skill_mobile.svg')
        check(u'PNG链·退出码', code, 0)
        check(u'PNG链·产出 PNG', os.path.isfile(png), True)
        check(u'PNG链·同时保留 SVG', os.path.isfile(svg), True)
        check(u'PNG链·日志列出产物', u'产物：' in log, True)
        check(u'PNG链·不报降级', u'PNG 未生成' in log, False)
        check(u'PNG链·无 ModuleNotFoundError',
              u'ModuleNotFoundError' in log, False)
        if os.path.isfile(png):
            with open(png, 'rb') as f:
                check(u'PNG链·PNG 魔数正确',
                      f.read(8) == b'\x89PNG\r\n\x1a\n', True)
        if os.path.isfile(svg):
            check(u'PNG链·SVG 完整（末行 </svg>）',
                  _read(svg).rstrip().endswith('</svg>'), True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 链 ②：没装 Pillow → 出 SVG、不崩、给指引 ──────────────────
def test_svg_chain():
    d = _tmp()
    try:
        code, log = _run_render(no_site=True, out_dir=d)
        png = os.path.join(d, 'skill_mobile.png')
        svg = os.path.join(d, 'skill_mobile.svg')
        check(u'SVG链·退出码 0（降级不算失败）', code, 0)
        check(u'SVG链·出 SVG', os.path.isfile(svg), True)
        check(u'SVG链·不出 PNG', os.path.isfile(png), False)
        check(u'SVG链·日志说明降级', u'PNG 未生成' in log, True)
        check(u'SVG链·日志给安装指引',
              u'pip install -r requirements.txt' in log, True)
        check(u'SVG链·已不再抛异常',
              u'ModuleNotFoundError' in log, False)
        if os.path.isfile(svg):
            txt = _read(svg)
            check(u'SVG链·SVG 完整（末行 </svg>）',
                  txt.rstrip().endswith('</svg>'), True)
            check(u'SVG链·SVG 有实质内容', '<text' in txt, True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── shoot._pick_artifact：PNG 优先 / SVG 兜底 / 都没有 ─────────
def test_pick_artifact():
    from walkkcal import shoot
    d = _tmp()
    try:
        png = os.path.join(d, 'skill_mobile_C.png')
        svg = os.path.join(d, 'skill_mobile_C.svg')

        p, deg = shoot._pick_artifact(d, '_C')
        check(u'挑选·都缺 → None', p, None)
        check(u'挑选·都缺 → 非降级', deg, False)

        with open(svg, 'w', encoding='utf-8') as f:
            f.write('<svg/>')
        p, deg = shoot._pick_artifact(d, '_C')
        check(u'挑选·只有 SVG → 选 SVG',
              os.path.basename(p or u''), u'skill_mobile_C.svg')
        check(u'挑选·只有 SVG → 标记降级', deg, True)

        with open(png, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')
        p, deg = shoot._pick_artifact(d, '_C')
        check(u'挑选·两者都在 → 选 PNG',
              os.path.basename(p or u''), u'skill_mobile_C.png')
        check(u'挑选·PNG 在场 → 非降级', deg, False)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    print(u'── 渲染产物降级链（PNG 链 / SVG 链）──\n')
    test_png_chain()
    print()
    test_svg_chain()
    print()
    test_pick_artifact()
    print()
    if _fail:
        print(u'❌ %d 项失败：%s' % (len(_fail), u'、'.join(_fail)))
        sys.exit(1)
    print(u'✅ 全部通过')
