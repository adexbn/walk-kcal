# -*- coding: utf-8 -*-
"""
带校验的 SVG 输出容器
=====================

★ 为什么需要这个
手写 SVG 时，格式符个数与参数个数不匹配，Python 报的错是：
    TypeError: must be real number, not str
    TypeError: not all arguments converted during string formatting
**两个都指不到真正出错的那一行** —— 因为 `%s` 会把后面的数值当字符串吃掉，
错误发生时已经离源头很远。这个坑我踩了 5 次以上（贝塞尔 C/Q 的坐标数、
徽章多传了一个参数、多边形点数不定…）。

解法：`o` 不再用裸 list，而用这个 `OList`。它继承 list，
`append(fmt, *vals)` 会先数格式符个数，不匹配就报出**准确的行号和内容**。
"""

import re

_FMT_RE = re.compile(r'%[-+0-9.]*[a-zA-Z]')


class OList(list):
    def append(self, fmt, *vals):
        if vals:
            n = len(_FMT_RE.findall(fmt))
            if n != len(vals):
                import sys
                lineno = sys._getframe(1).f_lineno
                raise ValueError(
                    u'第 %d 行：格式符 %d 个 vs 参数 %d 个\n    %s'
                    % (lineno, n, len(vals), fmt[:110]))
            list.append(self, fmt % vals)
        else:
            # 无参数：直接追加（调用方自己拼好了字符串）
            list.append(self, fmt)
