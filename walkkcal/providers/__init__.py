# -*- coding: utf-8 -*-
u"""providers —— 可插拔的外部服务实现

    geo_amap.py           高德地图（默认，已实测）
    geo_baidu.py          百度地图（桩：接口留好，填 Key 即可）
    geo_template.py       ★ 接新地图，照这个抄

    corpus_xiachufang.py  下厨房语料库（默认，已实测）
    corpus_dianping.py    大众点评（桩：买到了数据就填这里）
    corpus_template.py    ★ 接新语料，照这个抄

设计原则
────────
1. **接口在 walkkcal/geo.py 与 walkkcal/corpus.py，实现在这里**
   —— 主流程只依赖接口，不依赖任何具体服务商。
2. **每个 provider 只做「协议适配」**：把对方的数据结构翻译成我们的标准结构，
   不掺任何业务判断（判档/选店/渲染都在上层）。
3. **桩文件也是文档**：geo_baidu.py 里写清了要填哪几个字段，
   不需要读源码就知道怎么接。
"""
