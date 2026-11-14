"""BetterBuy V0.2 —— 自动商品识别与比价系统。

模块划分（便于后续扩展，不改动旧的 scraper.py / scoring.py）::

    models.py          统一数据模型（含每个字段的来源与采集时间）
    netguard.py        外网访问安全检查（禁止内网/本地地址）
    http_client.py     统一请求层：限速、robots、错误分类、短链解析
    cache.py           SQLite 缓存与采集日志、价格历史
    normalize.py       商品名称/品牌/型号/参数的标准化与同款判定
    adapters/          按平台拆分的采集适配器
    discovery/         竞品发现（当前仅 Steam 搜索页）
    recommend.py       分类推荐（性价比/规格/预算/口碑/综合）
    advisor.py         购买建议（规则版；大模型为可选增强）
"""

__version__ = "0.2.0"
