# BetterBuy 智选 · 部署说明

这个目录是 `product_compare_project` 的**部署副本**，只用于上传 GitHub 和
Streamlit Community Cloud。原项目目录里的文件一个都没动。

- 本地原项目：`D:\personal\项目\product_compare_project`（开发用，含数据库、备份、截图）
- 部署副本：`D:\personal\项目\betterbuy_deploy`（本目录，干净可上传）

---

## 一、和原项目相比，只改了三处

| 文件 | 改动 | 为什么 |
| --- | --- | --- |
| `betterbuy/secrets_bridge.py` | 新增 | Streamlit Cloud 的 Secrets 只进 `st.secrets`，不进 `os.environ`；本项目所有适配器都从环境变量读 `BETTERBUY_*`。加这一层，云端填了密钥才真正生效。本地没有 `secrets.toml` 时它是空操作。 |
| `app.py` | 导入区末尾加了 3 行（调用 `_apply_secrets()`）；另把一处展示用的 `scripts\check_openapi.py` 改成 `scripts/check_openapi.py`（反斜杠在 Linux 下是错的） | 启动时调用上面的桥接；顺手修掉唯一的 Windows 风格路径。 |
| `README.md` | 顶部加了一行指向本文件的说明 | 提示读者先看部署步骤。 |

其余代码与测试**逐字节相同**。`requirements.txt` 未改动（实测依赖已齐全，见第三节）。

---

## 二、安全检查结果

已按三条线扫过整个副本：

1. **密钥**：没有 `.env`、`secrets.toml`、`.pem`、`.key`，也没有硬编码的 AppKey / AppSecret / Token。
   所有凭据只从环境变量读（`betterbuy/openapi/credentials.py`），界面上最多显示前 4 位。
2. **个人信息**：没有手机号、邮箱、真实姓名、学号。测试与示例里的商品 ID（如 `100012043978`）
   都是京东公开商品页的示例编号，不含账号信息。
3. **绝对路径**：没有任何 `C:\` / `D:\` 硬编码。数据库路径由
   `Path(__file__).parent.parent / "data"` 推导，Linux 上自动落在应用目录内。

上传前如果想自己再确认一遍，在本目录执行：

```bash
grep -rn "D:\\\\\|C:\\\\\|/Users/" --include=*.py .
grep -rniE "app_?secret|access_?token|sk-[a-z0-9]{10}" --include=*.py .
```

两条命令都不应命中真实凭据。

---

## 三、`requirements.txt` 是否够用

```
beautifulsoup4>=4.12,<5
pandas>=2.0,<4
requests>=2.31,<3
streamlit>=1.39,<3
```

对着源码里的第三方导入逐条核过：`requests`、`bs4`、`pandas`、`streamlit` 四项覆盖全部，
没有遗漏。两处容易踩坑的地方已经确认过：

- HTML 解析统一用 `html.parser`（见 `scraper.py`、`betterbuy/adapters/*`），**不需要 lxml**。
- `playwright` 只在 `scraper.py` 的浏览器兜底分支里懒加载，云端不装也能正常跑（该分支会给出提示）。

所以 `requirements-browser.txt` 不上传，避免云端多装几百 MB 还编译失败。

---

## 四、本地先验一遍（可选，但建议）

```bash
cd D:\personal\项目\betterbuy_deploy
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -t tests
.venv\Scripts\python -m streamlit run app.py
```

测试期望结果：`Ran 319 tests ... OK (skipped=2)`（两个 skip 是需要联网的实时采集用例）。

---

## 五、上传 GitHub（私有仓库已建好）

在**本目录**里初始化并推送（不要在原项目目录里做）：

```bash
cd D:\personal\项目\betterbuy_deploy
git init
git branch -M main
git add .
git status            # 先看一眼，确认没有 data/、__pycache__、.env
git commit -m "BetterBuy 智选：Streamlit 部署版"
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git push -u origin main
```

`git status` 里应该只有 `app.py`、`betterbuy/`、`tests/`、`scripts/`、`docs/`、
`requirements.txt`、`README.md`、`DEPLOY.md`、`.gitignore`、`.streamlit/config.toml`、3 张截图。

---

## 六、Streamlit Community Cloud 部署

1. 打开 https://share.streamlit.io ，用 GitHub 登录并授权这个私有仓库。
2. **New app** → 选仓库和分支 `main` → Main file path 填 `app.py`。
3. **Advanced settings** 里 Python 版本选 **3.11** 或 **3.12**（本地验证用的是 3.12）。
4. 不填 Secrets 也能直接 Deploy：零成本模式下所有电商适配器会明确显示"未配置凭据"，
   Steam 比价、理性消费分析、绿色建议、风险提示全部可用。
5. 首次构建约 2–5 分钟，日志里出现 `You can now view your Streamlit app` 即成功。

### 以后要接京东/淘宝/拼多多的密钥

在 App → Settings → **Secrets** 里按**平铺**写法填（不要写 `[section]`）：

```toml
BETTERBUY_JD_APP_KEY = "..."
BETTERBUY_JD_APP_SECRET = "..."
BETTERBUY_TAOBAO_APP_KEY = "..."
BETTERBUY_TAOBAO_APP_SECRET = "..."
BETTERBUY_PDD_CLIENT_ID = "..."
BETTERBUY_PDD_CLIENT_SECRET = "..."
```

保存后应用会自动重启，`secrets_bridge.py` 把它们注入环境变量，第五节那个
"电商平台接入"标签页就会从"未配置"变成"已配置"。

> 注意：免费额度按平台账号算，与部署在哪台机器无关。这些密钥是你个人账号的，
> 不要把仓库改成公开，也不要贴进截图。

---

## 七、云端会遇到的限制（提前说清，免得上线后误会）

| 现象 | 原因 | 影响 |
| --- | --- | --- |
| 价格历史、采集日志在重启后清空 | 免费档文件系统是临时的，容器重启即重置 | 比价本身不受影响，只是看不到历史曲线 |
| 闲置一段时间后首次打开很慢 | 免费档应用会休眠，访问时冷启动 | 等 20–60 秒即可，无需处理 |
| 京东/淘宝/拼多多商品页抓取可能失败 | 云服务器 IP 段被电商风控拦截（返回 403/验证页） | 这是平台策略，代码不绕过；页面会显示"访问受限"并给出官方跳转链接 |
| 每日请求上限按 UTC 自然日重置 | `betterbuy/zero_cost.py` 按日期分桶计数 | 超限后当天不再发外部请求，页面会提示，不会产生费用 |

以上都在应用内的"零成本与配额""说明与限制"两个标签页里有对应说明，不是隐藏行为。

---

## 八、后续更新

改完代码后重复：

```bash
cd D:\personal\项目\betterbuy_deploy
# 从原项目同步改动（示例：只同步业务模块）
cp D:\personal\项目\product_compare_project\betterbuy\*.py betterbuy\
git add -A && git commit -m "同步改动" && git push
```

Streamlit Cloud 会自动重新部署。`app.py` 里那 3 行桥接记得保留，否则云端密钥失效。
