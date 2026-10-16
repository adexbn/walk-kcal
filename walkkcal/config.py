# -*- coding: utf-8 -*-
u"""config —— 配置/密钥的唯一入口（★ **Key 不许写在 `.py` 里**）

为什么要这一层
──────────────
2026-10-16 之前，高德 Key 以明文写在若干脚本里（`K = '3fe5…'`）。
明文密钥的问题不是「能不能用」，而是**它会跟着文件一起被复制、提交、截图**。
现在：**Key 只存在于配置文件或环境变量里**，代码里只剩「怎么取」这一个函数。

取值顺序（**环境变量永远优先**）
────────────────────────────────
    ① 环境变量                —— 宿主/CI 注入，只活在进程里（最高优先）
    ② `WALKKCAL_CONFIG` 指向的 JSON —— 显式指定；**设了就用它，不再看下面**
    ③ `<仓库>/config.local.json`           ← 真正的项目配置文件
    ④ `<仓库>/walkkcal/config.local.json`
    ⑤ `<仓库>/../internal/config.local.json`  ← 仓库外的工作区副本

文件格式：**JSON**
──────────────────
    {"AMAP_KEY": "…", "MCD_MCP_TOKEN": "…", "BAIDU_AK": "…"}

★ 为什么是 JSON 而不是 YAML：本项目**零第三方依赖**是硬约束，YAML 要拉
  `PyYAML`；`json` 是标准库。见 `SKILL.md` 的「运行前提」。

★ 为什么 `load()` 会把值写进 `os.environ`：`shoot.py` 用 subprocess 调
  `render.py`，只把 `os.environ` 传给子进程。配置不落进环境变量的话，
  子进程读不到 Key。用 `setdefault` —— **不会覆盖已有的环境变量**。

提交前
──────
`config.local.json` **永远不进版本库**（见 `.gitignore`）。
自检：`python ../internal/_check_secrets.py`（退出码 1 = 发现明文密钥）。
"""
from __future__ import annotations

import io
import json
import os

ENV_PATH = 'WALKKCAL_CONFIG'

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

# ★ 会被屏蔽的变量名（铁律 3：报错文本里的 Key 必须屏蔽）。
#   判据用**后缀**，所以将来的 `FOO_KEY` / `BAR_TOKEN` 自动覆盖。
SECRET_SUFFIXES = ('KEY', 'TOKEN', 'AK', 'SECRET', 'PASSWORD')
# 值太短就别参与替换 —— 否则会把正常文字打成马赛克
_MIN_SECRET_LEN = 8

_cache = None          # 最近一次读到的配置文件内容（None = 还没读过）
_path = None           # 生效的配置文件路径（None = 没有配置文件）
_missing = None        # WALKKCAL_CONFIG 指向但不存在时的路径（用于报错）


def candidate_paths():
    u"""按优先级列出候选配置文件（绝对路径）。`WALKKCAL_CONFIG` 一旦设了就独占。"""
    env = (os.environ.get(ENV_PATH) or u'').strip()
    if env:
        return [os.path.abspath(env)]
    return [os.path.join(_REPO, 'config.local.json'),
            os.path.join(_HERE, 'config.local.json'),
            os.path.join(os.path.dirname(_REPO), 'internal',
                         'config.local.json')]


def _read(path):
    u"""读一个 JSON 配置文件；不是 dict / 读不动 → 返回 `{}`（**不抛异常**）。"""
    try:
        with io.open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except (IOError, OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    # 下划线开头的键是注释（见 config.example.json），不进环境变量
    return {k: v for k, v in doc.items()
            if not str(k).startswith('_') and isinstance(v, (str, int, float))}


def load(force=False):
    u"""读配置并**注入环境变量**（已存在的环境变量优先，不被覆盖）。

    幂等：重复调用只读一次文件。`force=True` 重新读（测试用）。
    """
    global _cache, _path, _missing
    if _cache is not None and not force:
        return _cache
    _cache, _path, _missing = {}, None, None
    for i, p in enumerate(candidate_paths()):
        if os.path.isfile(p):
            vals = _read(p)
            if vals:
                _cache, _path = vals, p
                break
            continue                  # 文件在、但空或坏 → 继续看下一个候选
        if i == 0 and (os.environ.get(ENV_PATH) or u'').strip():
            _missing = p              # 显式指定却不在 → 记下来，供 describe() 报出来
    for k, v in _cache.items():
        if v is None or v == u'':
            continue
        os.environ.setdefault(str(k), u'%s' % (v,))
    return _cache


def path():
    u"""当前生效的配置文件绝对路径（没有则 `None`）。"""
    load()
    return _path


def missing_path():
    u"""`WALKKCAL_CONFIG` 指向了但文件不存在时，返回那个路径。"""
    load()
    return _missing


def get(name, default=u''):
    u"""取配置值：**环境变量 > 配置文件 > default**。"""
    v = os.environ.get(name)
    if v:
        return v
    v = load().get(name)
    return default if v in (None, u'') else v


def enable_live():
    u"""麦当劳「实时找店」是否开启（`WALKKCAL_MCD_LIVE`）。

    ★ 默认 **关**。开着跑实时需要两个 Key 缺一不可：
      `MCD_MCP_TOKEN`（调得动 MCP）+ `AMAP_KEY`（把坐标翻译成 city/keyword）。
      缺任何一个都**回退快照**，但原因会写进 `McdPack.last_note`
      → `registry.notes()` → 用户可见的文案（不再是静默的）。
    """
    return (os.environ.get('WALKKCAL_MCD_LIVE') or u'').strip().lower() in (
        '1', 'true', 'yes', 'on')


def secret_values():
    u"""所有「可能出现在报错文本里」的密钥值，返回 `[(值, 变量名)]`，**长的在前**。

    给 `redact()` 用：一次替换掉整段报错里的 Key（铁律 3）。
    来源 = 配置文件的值 + 名字像密钥的环境变量。
    """
    found = {}
    for name, val in load().items():
        s = u'%s' % (val,)
        if len(s) >= _MIN_SECRET_LEN:
            found[s] = str(name)
    for name, val in os.environ.items():
        if not val or len(val) < _MIN_SECRET_LEN:
            continue
        up = name.upper()
        if any(up.endswith(sfx) for sfx in SECRET_SUFFIXES):
            found.setdefault(val, name)
    return sorted(found.items(), key=lambda kv: -len(kv[0]))


def redact(text):
    u"""★ 铁律 3：把文本里出现过的密钥替换成 `<已屏蔽 XXX>`。

    **任何要打印/返回/写日志的文本，都先过它。**
    为什么放在 config 里：密钥的**唯一集合**就在这里，屏蔽逻辑跟着它走才不会漂。
    """
    s = u'%s' % (text,)
    for val, name in secret_values():
        if val in s:
            s = s.replace(val, u'<已屏蔽 %s>' % name)
    return s


def describe():
    u"""一行说明「配置从哪来、有哪些键」—— **只说有没有，不说值**。"""
    load()
    p = _path or u'（没有配置文件，只读环境变量）'
    keys = sorted(k for k in load().keys())
    have = sorted(k for k in keys if get(k))
    return (u'配置：%s\n  键 %s\n  有值 %s'
            % (p, u' / '.join(keys) or u'—', u' / '.join(have) or u'—'))


if __name__ == '__main__':
    import sys
    sys.stdout.write(describe() + u'\n')
    miss = missing_path()
    if miss:
        sys.stdout.write(u'⚠ WALKKCAL_CONFIG 指向的文件不存在：%s\n' % miss)
    for p in candidate_paths():
        sys.stdout.write(u'  %s %s\n'
                         % (u'✓' if os.path.isfile(p) else u'·', p))
    sys.stdout.write(u'麦当劳实时路径：%s\n'
                     % (u'开' if enable_live() else u'关（默认）'))
