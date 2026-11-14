"""品类知识库：理性消费分析与绿色消费建议的共用事实层。

**这个文件里没有商品数据。** 它只有「品类层面的常识规则」：
某类商品通常要看哪些参数、有什么兼容性坑、什么情况下可以先修/先升级/买二手。
所有价格区间都标注为「经验参考区间」，只用于判断预算是否落在该品类的常见范围，
**不是任何具体商品的报价，也不来自任何实时接口**。

设计原则：
1. 只写可公开验证的通用事实（例如「人体工学椅要看腰托与气压棒认证」）。
2. 不写具体品牌的好坏评价，避免变成软广或诋毁。
3. 不写碳减排量、节约金额这类无法核实的量化指标。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "Category", "SpecHint", "CompatRule", "GreenPath", "CategoryMention",
    "CATEGORIES", "OTHER_KEY", "PRICE_BAND_NOTE",
    "find_category", "find_purchase_target", "all_names", "get_category",
    "key_of_name",
]

#: 表示「要买」的动词。用于把「购买目标」和「已有设备」区分开。
#: 注意包含裸「买」——中文最常说「想花 800 买人体工学椅」。
_BUY_VERBS = ("购买", "买一个", "买一款", "买个", "买台", "买", "入手",
              "想换", "换一个", "换个", "换台", "选一个", "挑一个", "推荐一个",
              "需要一", "考虑买", "准备买", "打算买")

#: 表示「这是我已有的东西」的标记。出现在品类词前面时不当作购买目标。
_OWN_MARKERS = ("我的", "我有", "我现在用", "现在用", "手上", "现有的", "原来",
                "旧的", "自己的", "室友", "家里", "实验室")

#: 购买动词与品类词之间允许的最大距离（归一化后的字符数）。
_VERB_WINDOW = 14

#: 价格区间的性质说明，界面必须原样展示，避免被当成实时价格。
PRICE_BAND_NOTE = (
    "下列价格为「该品类的常见价位经验区间」，用于判断你的预算是否落在合理范围，"
    "**不是任何具体商品的报价，也不来自实时接口**。真实价格以商品页为准。"
)

OTHER_KEY = "other"


@dataclass(frozen=True)
class SpecHint:
    """一个需要重点关注的参数。"""

    name: str          # 参数名，如「面板类型」
    why: str           # 为什么重要（一句话，说人话）
    watch_out: str = ""  # 常见坑


@dataclass(frozen=True)
class CompatRule:
    """一条兼容性检查规则。

    ``ask`` 是「需要向用户确认什么」，而不是直接判定不兼容——
    我们拿不到用户设备信息时只能给出待确认清单。
    """

    need: str          # 需要确认的接口/规格，如「笔记本是否有 HDMI 或全功能 Type-C」
    why: str           # 为什么
    fatal_if_missing: bool = False   # 缺了是否会导致完全不能用


@dataclass(frozen=True)
class GreenPath:
    """一条「更省钱/更环保」的替代路径。"""

    title: str
    detail: str
    check: str          # 让用户自查的一句话
    family: str         # computer / daily / electronics / study / entertainment
    priority: int = 2   # 1=最先考虑 2=可选


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    aliases: tuple[str, ...]
    family: str                      # 决定用哪套绿色建议
    price_low: float                 # 经验区间下限（元）
    price_high: float                # 经验区间上限（元）
    specs: tuple[SpecHint, ...] = ()
    compat: tuple[CompatRule, ...] = ()
    green: tuple[GreenPath, ...] = ()
    durable: bool = True             # 是否属于耐用品（影响「先修再买」建议）
    essential_hint: str = ""         # 什么情况下它确实是必需品（避免误劝退）


def _s(name: str, why: str, watch: str = "") -> SpecHint:
    return SpecHint(name, why, watch)


def _c(need: str, why: str, fatal: bool = False) -> CompatRule:
    return CompatRule(need, why, fatal)


def _g(title: str, detail: str, check: str, family: str, priority: int = 2) -> GreenPath:
    return GreenPath(title, detail, check, family, priority)


CATEGORIES: dict[str, Category] = {
    # ---------------- 电脑设备 ----------------
    "laptop": Category(
        key="laptop", name="笔记本电脑",
        aliases=("笔记本", "笔记本电脑", "轻薄本", "游戏本", "laptop", "notebook",
                 "macbook", "全能本", "二合一"),
        family="computer", price_low=3000, price_high=9000, durable=True,
        specs=(
            _s("内存容量与是否可扩展", "8GB 在多开网页 + 网课时就会吃紧；板载内存无法后期加装",
               "「板载 LPDDR」= 终身不能升级，买小了只能整机换"),
            _s("硬盘接口与数量", "能否加第二块硬盘直接决定这台机器能用几年",
               "只有一个 M.2 插槽时，后期扩容只能换不能加"),
            _s("屏幕色域与分辨率", "写作业影响不大，做设计/剪视频影响很大",
               "只写「高清屏」不写 sRGB 覆盖率的，通常是低色域"),
            _s("散热与整机重量", "游戏本性能强但重，通勤背一学期很具体",
               "标称重量常不含电源适配器，游戏本适配器往往另有 0.5–1kg"),
            _s("电池容量与续航口径", "教室插座不够时续航比性能更影响体验",
               "厂商标称续航多为本地视频播放，实际办公常打对折"),
        ),
        compat=(
            _c("常用软件是否只支持 Windows（如部分专业软件、实验课工具）",
               "macOS 上部分课程软件需要虚拟机或实验室机器"),
            _c("是否需要外接显示器/投影（接口类型与数量）",
               "只有 Type-C 接口的机型需要扩展坞，是一笔额外支出"),
        ),
        green=(
            _g("先看能不能加内存/换硬盘",
               "很多 3–5 年的笔记本卡顿来自硬盘老化或内存不足，换一块固态 + 加内存的成本通常远低于换整机。",
               "你的机器内存插槽是否还有空位？硬盘是 SATA 还是 M.2？", "computer", 1),
            _g("先排除软件与散热问题",
               "重装系统、清灰换硅脂、换电池，往往能让旧机恢复可用。",
               "卡顿是开机就卡，还是开多个程序才卡？风扇声音是否明显变大？", "computer", 1),
            _g("二手/官翻可以考虑，但要验三件事",
               "笔记本是耐用品，成色好的二手性价比通常高于新机；风险集中在电池健康度、屏幕坏点与是否为拼装机。",
               "能否当面验机：电池循环次数、屏幕纯色坏点、序列号与保修状态？", "computer"),
            _g("按需求选档，不必追顶配",
               "写论文、上网课、跑代码 与 3A 游戏 对硬件的要求差一个档位，多花的钱可能完全用不到。",
               "你最重的任务是哪一个？它需要独显吗？", "computer", 1),
        ),
        essential_hint="课程明确要求自带电脑、或现有设备已无法完成作业时，它属于必需品。",
    ),
    "monitor": Category(
        key="monitor", name="显示器",
        aliases=("显示器", "屏幕", "外接屏", "monitor", "电竞屏"),
        family="peripheral", price_low=500, price_high=2500, durable=True,
        specs=(
            _s("分辨率与尺寸的匹配", "27 寸配 1080p 会有明显颗粒感，24 寸 1080p 反而更细腻"),
            _s("面板类型（IPS/VA/TN）", "影响可视角度与色彩，写代码看文档更在意文字清晰度"),
            _s("刷新率", "60Hz 够日常，144Hz 主要提升游戏与滚动流畅度", "高刷通常要配合显卡与线材才能跑满"),
            _s("接口与线材", "HDMI/DP/Type-C 决定能不能接你的设备，也决定能否一线连笔记本供电",
               "标称 Type-C 但只有 15W 输出的，不能给笔记本供电"),
            _s("支架调节（升降/旋转）", "长期低头看屏幕是颈肩问题的主因之一",
               "不可调支架往往需要另买支架臂，是一笔额外支出"),
        ),
        compat=(
            _c("你的笔记本/台式机有哪些视频输出接口", "接口不匹配需要转接头，部分转接头不支持高刷新率", True),
            _c("是否需要 Type-C 一线连（同时传画面与供电）", "这决定你能否少插一根电源线"),
            _c("桌面深度是否放得下该尺寸", "27 寸以上建议桌深 ≥ 60cm，否则视距过近"),
        ),
        green=(
            _g("先确认旧显示器是否只是接口不够",
               "很多「想换屏」的实际需求是「想接第二块屏」，加一个转接或支架臂就能解决。",
               "你是嫌它小、嫌它糊，还是只是想多一块屏？", "computer", 1),
            _g("二手显示器是风险较低的二手品类",
               "显示器技术迭代慢，成色好的二手主要风险是坏点与背光老化。",
               "能否当面验：纯色背景看坏点、灰阶看漏光？", "computer"),
        ),
    ),
    "keyboard": Category(
        key="keyboard", name="键盘",
        aliases=("键盘", "机械键盘", "keyboard", "客制化键盘"),
        family="peripheral", price_low=100, price_high=800, durable=True,
        specs=(
            _s("配列（87/98/104 键）", "决定桌面占用与是否需要数字小键盘", "小配列没有独立方向键与功能键，需要适应"),
            _s("轴体类型（线性/段落/静音）", "影响手感与噪音，宿舍场景噪音很关键"),
            _s("连接方式（有线/蓝牙/2.4G）", "多设备切换看的是蓝牙通道数"),
            _s("键帽材质与是否热插拔", "热插拔意味着以后可以只换轴不换键盘"),
        ),
        compat=(
            _c("是否需要连接多台设备（笔记本 + 平板 + 手机）", "需要多模键盘，单模有线只能接一台"),
            _c("使用场景是否安静（宿舍/图书馆）", "段落轴与青轴噪音明显，可能影响室友"),
        ),
        green=(
            _g("先修再换",
               "键盘最常见的故障是单个轴体失灵或进灰，热插拔键盘换个轴即可，成本很低。",
               "是整块失灵，还是某几个键不灵？", "computer", 1),
            _g("键帽与轴体可以单独换",
               "手感不满意不一定要换整把键盘，换键帽或换轴是更省的路径。",
               "你不满意的是手感、声音，还是布局？", "computer"),
        ),
    ),
    "mouse": Category(
        key="mouse", name="鼠标",
        aliases=("鼠标", "mouse", "无线鼠标", "游戏鼠标"),
        family="peripheral", price_low=50, price_high=500,
        specs=(
            _s("握持方式与尺寸", "大手用小鼠会酸痛，这是最容易被忽略的参数"),
            _s("重量", "长时间使用轻量鼠标更省力"),
            _s("连接与回报率", "办公 125Hz 够用，游戏才需要高回报率"),
        ),
        compat=(
            _c("是否需要多设备切换", "多模鼠标可一键切换笔记本与平板"),
        ),
        green=(
            _g("先换微动或清洁",
               "鼠标最常见的故障是单击变双击（微动老化），清洁或换微动可以继续用很久。",
               "是单击变双击，还是完全没反应？", "computer", 1),
        ),
    ),
    "headphone": Category(
        key="headphone", name="耳机",
        aliases=("耳机", "headphone", "耳麦", "蓝牙耳机", "降噪耳机", "tws"),
        family="electronics", price_low=100, price_high=1500,
        specs=(
            _s("佩戴方式（头戴/入耳/半入耳）", "直接影响长时间佩戴舒适度", "入耳式久戴可能不适，宿舍学习更看重舒适"),
            _s("主动降噪", "图书馆与宿舍环境差异大，降噪的实际价值取决于场景"),
            _s("延迟与编解码", "看视频影响小，打游戏与练琴对延迟敏感"),
            _s("电池与是否可换电池", "耳机坏掉通常先坏电池"),
        ),
        compat=(
            _c("你的设备是否有对应蓝牙版本/接口", "老设备可能只支持 SBC，音质与延迟都会打折"),
            _c("是否需要同时连接电脑和手机", "需要多点连接功能"),
        ),
        green=(
            _g("先换耳罩/耳塞套",
               "头戴耳机最常见的「变旧」是耳罩掉皮，更换耳罩成本很低。",
               "是声音变差，还是只是耳罩磨损？", "electronics", 1),
            _g("电池衰减可以先换电池",
               "真无线耳机的续航下降多为电池老化，部分型号可更换。",
               "续航下降幅度多大？是否还在保修期？", "electronics", 1),
        ),
    ),
    "storage": Category(
        key="storage", name="移动硬盘/U盘",
        aliases=("移动硬盘", "硬盘", "u盘", "U盘", "固态硬盘", "ssd", "移动固态", "存储卡"),
        family="peripheral", price_low=80, price_high=800,
        specs=(
            _s("接口与实测速度", "USB 3.2 Gen1 与 Gen2 差一倍速度，标称速度常是峰值"),
            _s("容量与单位换算", "标称 1TB 实际可用约 931GB，属正常换算差异"),
            _s("是否固态", "机械硬盘怕摔，随身携带更建议固态"),
        ),
        compat=(
            _c("你的设备接口类型与供电能力", "部分大容量移动硬盘需要额外供电", True),
            _c("是否需要跨 Windows / macOS 使用", "exFAT 可跨平台，NTFS 在 macOS 上默认只读"),
        ),
        green=(
            _g("先看是不是空间被临时文件占了",
               "系统更新缓存、微信/QQ 聊天文件、旧项目目录常占用大量空间。",
               "先按文件夹大小排个序，看看最大的三个是什么？", "computer", 1),
            _g("云盘 + 小容量移动盘组合往往够用",
               "不是所有资料都需要随身带，冷数据放云盘更省。",
               "你要带走的资料里，有多少是每周真的会打开的？", "computer"),
        ),
    ),
    "powerbank": Category(
        key="powerbank", name="充电宝",
        aliases=("充电宝", "移动电源", "powerbank"),
        family="electronics", price_low=60, price_high=300,
        specs=(
            _s("额定容量（不是标称容量）", "标称 20000mAh 的额定容量通常只有 12000mAh 左右"),
            _s("输出功率与协议", "能否给笔记本供电取决于是否支持 PD 45W 以上"),
            _s("是否可带上飞机", "民航规定看的是额定能量 Wh，不是 mAh"),
        ),
        compat=(
            _c("你的手机/笔记本支持哪些快充协议", "协议不匹配时会退到 5V 慢充", True),
            _c("是否需要给笔记本供电（功率要求）", "轻薄本通常需要 45W 以上"),
        ),
        green=(
            _g("先确认是不是线的问题",
               "充电慢、充不上，很多时候是线材不支持对应协议。",
               "换一根原装线是否恢复正常？", "electronics", 1),
        ),
    ),
    "phone": Category(
        key="phone", name="手机",
        aliases=("手机", "iphone", "安卓机", "smartphone"),
        family="electronics", price_low=1200, price_high=6000,
        specs=(
            _s("存储容量", "128GB 在照片与微信积累下很快吃紧"),
            _s("电池健康与续航", "二手手机最需要核实的就是电池"),
            _s("系统更新支持年限", "决定这台机器还能安全用几年"),
        ),
        compat=(
            _c("常用 App 与账号是否绑定特定系统", "跨系统迁移有成本"),
            _c("是否需要与现有耳机/手表/平板协同", "生态内协同通常更好"),
        ),
        green=(
            _g("先换电池再考虑换机",
               "多数「手机变卡/不耐用」的实际原因是电池老化，换电池成本远低于换机。",
               "电池健康度是多少？是否出现过意外关机？", "electronics", 1),
            _g("二手手机要验电池与维修史",
               "二手手机风险集中在电池、屏幕是否更换过、是否进过水。",
               "能否查到维修记录？电池健康度是否 ≥ 85%？", "electronics"),
        ),
    ),
    "tablet": Category(
        key="tablet", name="平板电脑",
        aliases=("平板", "平板电脑", "ipad", "tablet"),
        family="electronics", price_low=1500, price_high=5000,
        specs=(
            _s("是否需要配套笔与键盘", "配件往往占总支出很大一块"),
            _s("存储容量", "不可扩展，买小了无法补救"),
            _s("屏幕与手写延迟", "记笔记场景对延迟敏感"),
        ),
        compat=(
            _c("课程软件是否支持该平板系统", "部分专业软件只有桌面版", True),
        ),
        green=(
            _g("先确认笔记本是否能覆盖需求",
               "如果只是看课件与记笔记，很多人的笔记本已经够用，平板是补充而非替代。",
               "你打算用平板替代笔记本的哪一部分？", "electronics", 1),
        ),
    ),
    # ---------------- 生活/学习 ----------------
    "chair": Category(
        key="chair", name="人体工学椅",
        aliases=("人体工学椅", "工学椅", "椅子", "办公椅", "电脑椅", "chair"),
        family="daily", price_low=400, price_high=2500, durable=True,
        specs=(
            _s("腰托是否可调", "腰托是工学椅最核心的部件，不可调等于没有"),
            _s("气压棒等级与认证", "涉及安全，认 SGS / TUV / BIFMA 等认证标识",
               "只写「防爆气压棒」不给认证编号的，需要进一步核实"),
            _s("坐深与坐宽", "坐深不合适会压迫大腿，个子高的人尤其要看"),
            _s("扶手是否可调", "打字时扶手高度决定肩颈是否放松"),
            _s("网布 vs 海绵", "网布透气但支撑偏硬，海绵软但夏天闷"),
        ),
        compat=(
            _c("桌面高度与椅子最高坐高是否匹配", "椅子太高会导致手腕悬空"),
            _c("宿舍/房间能否放下（含后仰空间）", "后仰需要额外 20–30cm 空间"),
        ),
        green=(
            _g("先排除桌椅高度不匹配",
               "很多「坐久了腰疼」来自桌子太矮或太高，加一个脚踏或垫高桌腿就能缓解。",
               "坐下时小臂是否自然水平？脚掌是否完全着地？", "daily", 1),
            _g("靠垫 + 现有椅子的组合可以先试",
               "如果现有椅子只是缺腰托，一个可调腰靠的成本远低于换椅。",
               "你现在不舒服的位置是腰、颈，还是尾椎？", "daily", 1),
            _g("二手工学椅流通量大，但要验气压棒",
               "工学椅结构简单，二手主要风险是气压棒与网面塌陷。",
               "能否现场试坐并检查气压棒是否有异响？", "daily"),
        ),
        essential_hint="每天久坐超过 4 小时且有明确不适时，它属于改善健康的必要支出。",
    ),
    "lamp": Category(
        key="lamp", name="台灯",
        aliases=("台灯", "护眼灯", "阅读灯", "lamp"),
        family="daily", price_low=80, price_high=600,
        specs=(
            _s("照度与均匀度（国A/国AA）", "国AA 级对读写更合适，是可直接核对的指标"),
            _s("显色指数 Ra", "Ra ≥ 90 时纸张与屏幕颜色更自然"),
            _s("频闪与蓝光等级", "认 RG0 豁免级，可减少用眼疲劳"),
            _s("色温可调", "暖光适合睡前，冷白光更适合专注"),
        ),
        compat=(),
        green=(
            _g("先换灯泡而不是换整灯",
               "如果是可换灯泡的台灯，换成高显色 LED 灯泡成本很低。",
               "你的台灯是灯泡式还是一体式？", "daily", 1),
            _g("桌面位置可能比台灯本身更重要",
               "把灯放在写字手的对侧，能明显减少手部阴影。",
               "现在写字时，手的影子会挡住笔尖吗？", "daily"),
        ),
    ),
    "stationery": Category(
        key="stationery", name="学习用品/文具",
        aliases=("文具", "笔记本本子", "本子", "记事本", "笔", "记号笔", "荧光笔",
                 "打印纸", "便利贴", "文件夹", "书包", "文具盒", "学习用品",
                 "绘图工具", "草稿纸"),
        family="study", price_low=10, price_high=300,
        specs=(
            _s("消耗速度与实际用量", "文具是消耗品，按学期用量估算比按单价比较更有意义"),
            _s("是否需要特定规格", "课程可能指定纸张规格或笔的粗细"),
        ),
        compat=(),
        green=(
            _g("先清点已有存量",
               "文具是最容易「重复购买」的品类，抽屉里往往还有没用完的。",
               "同类物品你现在还有几件没拆封？", "study", 1),
            _g("优先选可替换芯/可补充的产品",
               "可换芯的笔、可补充的文件夹，长期成本更低。",
               "这个产品用完以后，是整件丢还是只换芯？", "study", 1),
            _g("按学期用量买，不按促销买",
               "大包装单价低但用不完就是浪费。",
               "这一学期你预计会用掉多少？", "study"),
        ),
        essential_hint="课程要求的指定教材、指定规格文具属于必需品。",
    ),
    "book": Category(
        key="book", name="书籍/教材",
        aliases=("书", "教材", "课本", "参考书", "小说", "book"),
        family="study", price_low=20, price_high=200,
        specs=(
            _s("版次与是否配套", "教材不同版次内容差异可能很大，课程通常指定版次"),
            _s("是否需要配套练习册/答案", "常被忽略的额外支出"),
        ),
        compat=(),
        green=(
            _g("先查图书馆与学长学姐",
               "教材是复用率最高的品类，图书馆藏量与二手流通都很充足。",
               "图书馆是否有该版次？同专业高年级是否愿意转让？", "study", 1),
            _g("电子版/二手优先",
               "教材使用周期通常只有一个学期。",
               "这门课之后你还会翻这本书吗？", "study", 1),
        ),
        essential_hint="课程明确要求且图书馆无馆藏时，属于必需品。",
    ),
    # ---------------- 娱乐 ----------------
    "game": Category(
        key="game", name="游戏",
        aliases=("游戏", "steam", "单机游戏", "联机游戏", "游戏本体", "dlc"),
        family="entertainment", price_low=30, price_high=300,
        specs=(
            _s("最低配置与推荐配置", "决定你的电脑能不能跑，是最该先看的参数"),
            _s("是否含 DLC", "本体之外的付费内容会影响完整体验的总成本"),
            _s("支持语言", "部分游戏只有界面中文，没有字幕中文"),
            _s("单机时长与是否支持联机", "影响这份钱能换回多少小时的体验"),
        ),
        compat=(
            _c("你的设备是否满足最低配置", "不满足时无法运行或体验极差", True),
            _c("是否需要与朋友联机（平台是否一致）", "跨平台联机并非都支持"),
        ),
        green=(
            _g("先看是否在打折周期",
               "数字游戏价格波动大，非急需时可以等折扣。",
               "这款游戏近半年是否出现过更低折扣？", "entertainment", 1),
            _g("先确认配置能否跑得动",
               "买完发现跑不动是最典型的浪费。",
               "你的显卡与内存是否达到推荐配置？", "entertainment", 1),
        ),
    ),
}

CATEGORIES[OTHER_KEY] = Category(
    key=OTHER_KEY, name="其他商品",
    aliases=(),
    family="daily", price_low=0, price_high=0,
    specs=(),
    compat=(),
    green=(
        _g("先确认需求是否明确",
           "没有明确用途的购买最容易变成闲置。想清楚「买来具体解决什么问题」。",
           "如果这件东西明天就到，你会在什么时候用它？", "daily", 1),
        _g("先查是否有可替代的现有物品",
           "很多东西家里已经有了，只是没想到。",
           "你现在是用什么方式解决同一个问题的？", "daily", 1),
    ),
)


def _norm(text: str) -> str:
    return (text or "").lower().replace(" ", "")


def _mentions(body: str) -> list[tuple[int, int, str, str]]:
    """找出文本里所有品类提及，去掉被更长别名包含的较短匹配。"""
    raw: list[tuple[int, int, str, str]] = []
    for key, category in CATEGORIES.items():
        if key == OTHER_KEY:
            continue
        for alias in category.aliases:
            token = _norm(alias)
            if not token:
                continue
            start = body.find(token)
            while start != -1:
                raw.append((start, start + len(token), key, alias))
                start = body.find(token, start + 1)
    kept: list[tuple[int, int, str, str]] = []
    for index, item in enumerate(raw):
        start, end = item[0], item[1]
        swallowed = False
        for other_index, other in enumerate(raw):
            if other_index == index:
                continue
            if other[0] <= start and other[1] >= end and (other[1] - other[0]) > (end - start):
                swallowed = True
                break
        if not swallowed:
            kept.append(item)
    # 完全相同位置的重复项（不同别名指向同一片段）只留一个
    seen: set[tuple[int, int]] = set()
    unique: list[tuple[int, int, str, str]] = []
    for item in kept:
        span = (item[0], item[1])
        if span in seen:
            continue
        seen.add(span)
        unique.append(item)
    return sorted(unique)


def _is_owned(text: str, start: int) -> bool:
    prefix = text[max(0, start - 6):start]
    return any(marker in prefix for marker in _OWN_MARKERS)


_CLAUSE_SPLIT = re.compile(r"[，,。；;、！!？?\n]")


def _clauses(body: str) -> list[str]:
    """按标点切成短句。

    这一步很关键：「人体工学椅，每天学习和打游戏用」里，「游戏」属于用途从句，
    不是购买目标。按标点切段后就只在同一段内找头词，不会跨段误判。
    """
    return [part for part in _CLAUSE_SPLIT.split(body) if part.strip()]


def find_purchase_target(text: str) -> tuple[str, str, list[str], str]:
    """区分「要买的东西」和「已经有的东西」。

    这是本模块最容易出错、也最影响结论的一步。真实需求里经常出现
    「我的笔记本是 XX，想买个外接显示器」——前半句说的是已有设备（兼容性依据），
    后半句才是购买目标。如果只做关键词匹配，会被「笔记本」抢走。

    判定顺序：
    1. 按标点切成短句。
    2. 优先找「含购买动词、且动词后有品类词」的短句，取该短句内**最靠右**的品类词
       （中文的修饰语在前、中心词在后，「打游戏用的显示器」头词是显示器）。
    3. 退而求其次取第一个含非已有品类词的短句，同样取最右。
    4. 只剩已有物品时，若有「换个新的」这类替换表达，目标就是已有物品本身。
    5. 都没有就返回 other。

    Returns:
        (品类 key, 命中的别名, 已提及的已有品类 key 列表, 判定依据说明)
    """
    body = _norm(text)
    if not body:
        return OTHER_KEY, "", [], "输入为空"

    clauses = _clauses(body)
    owned_keys: list[str] = []
    all_mentions: list[tuple[int, int, str, str, int]] = []   # 末位是所属短句序号
    for index, clause in enumerate(clauses):
        for start, end, key, alias in _mentions(clause):
            all_mentions.append((start, end, key, alias, index))
            if _is_owned(clause, start) and key not in owned_keys:
                owned_keys.append(key)

    if not all_mentions:
        return OTHER_KEY, "", [], "没有匹配到任何已收录的品类词"

    def rightmost(items):
        return max(items, key=lambda m: m[0])

    # 2) 购买动词所在的短句
    for verb in _BUY_VERBS:
        for index, clause in enumerate(clauses):
            cursor = clause.find(verb)
            while cursor != -1:
                verb_end = cursor + len(verb)
                window = [
                    m for m in all_mentions
                    if m[4] == index and not _is_owned(clause, m[0])
                    and m[0] >= verb_end and m[0] - verb_end <= _VERB_WINDOW
                ]
                if window:
                    start, end, key, alias, _ = rightmost(window)
                    note = f"按「{verb} + 同句最靠右的品类词」判定为「{alias}」"
                    if owned_keys:
                        note += ("；另有已有物品："
                                 + "、".join(get_category(k).name for k in owned_keys)
                                 + "（用于兼容性核对）")
                    return key, alias, owned_keys, note
                cursor = clause.find(verb, cursor + 1)

    # 3) 第一个含非已有品类词的短句
    for index, clause in enumerate(clauses):
        fresh = [m for m in all_mentions if m[4] == index and not _is_owned(clause, m[0])]
        if fresh:
            start, end, key, alias, _ = rightmost(fresh)
            return (key, alias, owned_keys,
                    f"没有找到购买动词，按第一个提到「{alias}」的短句判定")

    # 4) 只提到已有物品：「我的手机…想换个新的」
    if any(verb in body for verb in _BUY_VERBS) and ("新" in body or "换" in body):
        start, end, key, alias, _ = all_mentions[-1]
        return (key, alias, owned_keys,
                f"没有说出新的品类词，按「替换已有物品」判定目标为「{get_category(key).name}」")

    first = all_mentions[0]
    return (first[2], first[3], owned_keys,
            "只提到已有物品，没有明确的购买目标；按已有物品品类给出通用建议")


def find_category(text: str) -> tuple[str, str]:
    """兼容旧接口：只返回 (品类 key, 命中的别名)。新代码请用 :func:`find_purchase_target`。"""
    key, alias, _, _ = find_purchase_target(text)
    return key, alias


def get_category(key: str) -> Category:
    return CATEGORIES.get(key, CATEGORIES[OTHER_KEY])


def all_names() -> list[str]:
    """给界面用的品类名列表（按常见程度排序）。"""
    order = ["laptop", "monitor", "keyboard", "headphone", "chair", "phone",
             "storage", "tablet", "mouse", "lamp", "powerbank", "stationery",
             "book", "game", OTHER_KEY]
    return [CATEGORIES[k].name for k in order if k in CATEGORIES]


def key_of_name(name: str) -> str:
    """界面下拉框把品类名传回来时，转成内部 key。认不出就返回 other。"""
    target = (name or "").strip()
    for key, category in CATEGORIES.items():
        if category.name == target:
            return key
    return OTHER_KEY
