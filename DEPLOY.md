# BetterBuy 智选 · 部署说明

这个目录是**当前在用的代码**（GitHub `xh0493aur/BetterBuy` 就是它）。
它最初是 `product_compare_project` 的部署副本，2026-11-15 起拼多多的新功能
只在这里改，原开发目录没有同步——下面第一节那张表是"部署版 vs 原开发版"的历史差异。

- 原开发目录（作者本机）：含数据库、备份、截图，已落后于本目录
- 本仓库：即当前在用的代码，也是线上应用的来源

> 本文档里的 `cd BetterBuy` 指你 clone 下来的仓库根目录，路径按你自己的位置替换。

---

## 一、部署前那三处改动（历史记录）

| 文件 | 改动 | 为什么 |
| --- | --- | --- |
| `betterbuy/secrets_bridge.py` | 新增 | Streamlit Cloud 的 Secrets 只进 `st.secrets`，不进 `os.environ`；本项目所有适配器都从环境变量读 `BETTERBUY_*`。加这一层，云端填了密钥才真正生效。本地没有 `secrets.toml` 时它是空操作。 |
| `app.py` | 导入区末尾加了 3 行（调用 `_apply_secrets()`）；另把一处展示用的 `scripts\check_openapi.py` 改成 `scripts/check_openapi.py`（反斜杠在 Linux 下是错的） | 启动时调用上面的桥接；顺手修掉唯一的 Windows 风格路径。 |
| `README.md` | 顶部加了一行指向本文件的说明 | 提示读者先看部署步骤。 |

`requirements.txt` 自始至终没有改动：拼多多接入只用到 `requests`（已有），
没有引入新依赖。详见第三节。

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
python scripts/scan_secrets.py --tracked --history
```

它会扫明文密钥、被跟踪的 `.env` / `*.sqlite3`、本机绝对路径、邮箱手机号。
有 `error` 就退出码非 0，别 push。

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
cd BetterBuy
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -t tests
.venv\Scripts\python -m streamlit run app.py
```

测试期望结果：`Ran 319 tests ... OK (skipped=2)`（两个 skip 是需要联网的实时采集用例）。

---

## 五、上传 GitHub（公开或私有都可以）

在**本目录**里初始化并推送（不要在原项目目录里做）：

```bash
cd BetterBuy
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

## 五之二、仓库要公开怎么办

**结论：可以公开。密钥从来不在仓库里，公开仓库不会泄露任何凭据。**

要区分两类风险，它们的应对方式完全不同：

| 风险 | 公开仓库会怎样 | 应对 |
| --- | --- | --- |
| 密钥泄漏 | **不会**。AppSecret 只存在 Streamlit 的 Secrets 里，仓库里只有值为空的模板 | `.gitignore` 已挡 `.env` / `secrets.toml`；提交前跑自检脚本 |
| 额度被陌生人用掉 | **会**。任何人打开你的应用点一下搜索，花的都是你的调用次数 | 访问口令 + 公网演示模式（见下） |
| 代码被复制 | 会。公开就意味着可被 clone | 只在参赛/展示期公开；规则允许的话加 LICENSE 声明来源 |

### 三层防护（后两层是新加的，默认都不启用）

1. **密钥层**（始终有效）：密钥只放 Streamlit Cloud → Settings → Secrets，
   仓库里只有 `.streamlit/secrets.toml.example`（值全为空）。
   `betterbuy/secrets_bridge.py` 负责把 Secrets 注入环境变量。
2. **访问层**（可选）：在 Secrets 里加一行
   ```toml
   BETTERBUY_ACCESS_CODE = "自己定一串 8 位以上的口令"
   ```
   页面打开后先要口令，输对了才进得去。不设这一行则完全不拦截，本地开发不受影响。
3. **额度层**（可选，建议开）：在 Secrets 里加
   ```toml
   BETTERBUY_PUBLIC_DEMO = "1"
   ```
   每日上限从"电商各 200 / 合计 800"压到"电商各 20 / 合计 120"，
   页面顶部会注明这是公网演示模式。陌生人把额度刷完也不产生费用，第二天自动重置。

### 提交前自检（10 秒）

```bash
python scripts/scan_secrets.py --tracked --history
```

退出码 0 才 push。它会报四类东西：明文密钥字面量（`APP_SECRET = "..."`）、
`sk-` 开头的模型 Key、被 git 跟踪的 `.env` / `secrets.toml` / `*.sqlite3`、
本机绝对路径与邮箱手机号（后两类只是 warn，人工确认即可）。
`--history` 会连历史补丁一起扫，防止"提交过又删掉"的密钥留在 commit 里。

### GitHub 侧再开一道保险

仓库 → Settings → Code security：打开 **Secret scanning** 与 **Push protection**。
这样万一哪天真把密钥写进代码，GitHub 会在 push 时直接拦下（公开仓库免费）。

### 还有一条路：公开代码 + 私有实例

如果既想代码公开（便于评审、也符合参赛"真实可用"的要求），又不想让任何人碰你的密钥：

- 仓库 A（公开）：就是本目录，不带密钥，任何人可以 fork 自建。
- 部署 B（不公开分享链接）：Streamlit Cloud 上指向同一个仓库，把 Secrets 填上，
  链接只发给自己和评委。—— 代码仍然公开，但密钥只在 B 的 Secrets 里。

两种做法都不需要付费，区别只是"应用链接给不给别人"。选哪个取决于你要不要把 demo
链接放进参赛材料：**要给就用方案一（口令 + 演示模式），不给就用方案二。**

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
# 京东
BETTERBUY_JD_APP_KEY = "..."
BETTERBUY_JD_APP_SECRET = "..."

# 淘宝客（前缀是 TB，不是 TAOBAO）
BETTERBUY_TB_APP_KEY = "..."
BETTERBUY_TB_APP_SECRET = "..."
BETTERBUY_TB_ADZONE_ID = "..."

# 拼多多（多多进宝）
BETTERBUY_PDD_CLIENT_ID = "..."
BETTERBUY_PDD_CLIENT_SECRET = "..."
BETTERBUY_PDD_PID = "1234567_98765432"
```

保存后应用会自动重启，`secrets_bridge.py` 把它们注入环境变量，
"⑤ 电商平台接入"标签页里对应平台就会从"未配置"变成"已配置"。

> 注意：免费额度按平台账号算，与部署在哪台机器无关。这些密钥是你个人账号的，
> 不要把仓库改成公开，也不要贴进截图。

### 拼多多接入的四个要点

1. **三个值都得有**：`CLIENT_ID`、`CLIENT_SECRET`、`PID`。缺 `PID` 时客户端会直接判定"未配置"，
   不会发出任何请求（也就不会花额度）。
2. **PID 必须做授权备案**，否则关键词搜索返回 `50001 / sub_code 60001`。
   做法：在多多进宝用转链接口生成一次带 `generate_authority_url` 的链接并访问一次。
   如果备案时带了 `custom_parameters`，也要把同一份 JSON 配到
   `BETTERBUY_PDD_CUSTOM_PARAMETERS`，否则依然对不上。
3. **云端的出口 IP 不固定**，所以不要给应用开 IP 白名单（开了就会报 20005）。
4. **验证顺序**：先本地 `python scripts/check_pdd.py`（离线，只查配置），
   再 `python scripts/check_pdd.py --live`（真调一次搜索），最后 `--live --detail --link`。
   退出码直接告诉你卡在哪：2 未配置 / 3 凭证 / 4 权限 / 5 备案 / 6 限流 / 7 无结果 / 8 网络。

没有密钥时页面也不会崩：搜索面板会明确显示"未配置密钥：缺少 XXX"，
Steam 比价、理性消费分析等其余功能完全不受影响。

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
cd BetterBuy
# 从原项目同步改动（示例：只同步业务模块）
cp ../product_compare_project/betterbuy/*.py betterbuy/
git add -A && git commit -m "同步改动" && git push
```

Streamlit Cloud 会自动重新部署。`app.py` 里那 3 行桥接记得保留，否则云端密钥失效。
