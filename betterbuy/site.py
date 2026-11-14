"""项目身份信息：正式站点地址与代码仓库。

**这是唯一的正式地址来源。** 页面底部、部署文档、参赛材料都引用这里，
避免出现「文档里写了三个不同的网址」这种低级错误。

改动规则（重要）：
1. 正式站点就是下面这个 Streamlit Community Cloud 应用，禁止删除重建、
   禁止在应用设置里改 URL、禁止另建一个"正式"实例；
2. 换域名等于换官网，必须由项目负责人确认后再改这里；
3. 代码仓库保持 xh0493aur/BetterBuy，主文件是仓库根目录的 app.py。
"""
from __future__ import annotations

__all__ = ["OFFICIAL_URL", "GITHUB_REPO", "GITHUB_URL", "MAIN_FILE", "PROJECT_NAME"]

PROJECT_NAME = "BetterBuy 智选"

#: 正式官网（Streamlit Community Cloud 应用）。线上唯一入口。
OFFICIAL_URL = "https://betterbuy-ewtcn8eechfdqdrr2zymch.streamlit.app/"

#: 代码仓库（与上面的应用绑定，push 到 main 即触发重新部署）
GITHUB_REPO = "xh0493aur/BetterBuy"
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"

#: Streamlit 应用设置里的 Main file path。仓库是根目录布局，所以就是 app.py。
MAIN_FILE = "app.py"
