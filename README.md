# Agent Team · 智能体团队

**像聊天一样使用智能体：一个机器人就是一个独立的角色，多个机器人可以加入群聊或有界协作。**

> 状态：**v0.3.1 远程设备任务代理 MVP / 可运行的跨端 Web/PWA 原型**。现阶段不是 Windows `.exe`、Linux 安装包或 Android `.apk`；后续通过 Tauri 2 封装原生安装包。项目没有声称支持用 ChatGPT Plus / Claude Pro 等网页订阅直接调用模型。


## v0.3.1：手机远程给电脑下发任务（受限版）

现在可以把同一个 Agent Team 服务器部署在 HTTPS 域名后，手机和电脑 Web/PWA 访问同一服务端，**共享会话及任务状态**。受控电脑运行 `agent-team-worker`，通过出站 HTTPS 主动轮询云服务器的任务队列，执行白名单任务并将结果回传手机。

- 在手机打开 `/remote.html`，使用所有者访问口令创建电脑设备，获取仅显示一次的设备密钥。
- 电脑设置 `AGENT_TEAM_HUB_URL` 和 `AGENT_TEAM_DEVICE_TOKEN`，运行 `agent-team-worker --workspace <授权工作目录>`；可选 `--allow-write`，只允许往 `AgentTeamInbox` 写新便签。
- 支持：设备心跳/撤销、查询系统信息、列出授权目录、接收任务通知、保存便签；手机查看任务结果，并可以取消待执行任务、手动重试失败或中断任务；中断任务不会自动重跑。**没有远程 Shell、GUI 自动操作或原生 APK/.exe。**

使用前请读 [远程控制部署与安全说明](docs/REMOTE_CONTROL.md)，包括 TLS / VPN、权限边界和限制。现阶段不适合直接公网暴露裸服务。

## 已实现

- **统一聊天体验**：响应式界面同时适配桌面浏览器（Windows、Linux）与 Android 浏览器；支持 HTTPS/localhost 下安装 PWA。
- **一人一智能体**：独立名称、备注、头像、系统提示词、模型 ID、凭据和 Skills。
- **群聊、点名、协作**：私聊、`@智能体名称`/`@备注`、圆桌聊天、串行协作（后一个成员能读取前一个成员的回复）。
- **多模型 / 多 Key**：一个凭据可被多个机器人复用，也可分别绑定不同凭据；支持 OpenAI、DeepSeek、OpenRouter、Anthropic Claude、Google Gemini、Ollama 和兼容 Chat Completions 的模型服务。
- **本地记录**：SQLite 保存机器人、对话、技能和消息。API Key 使用 Fernet 加密，前端不能读取保存的密钥原文。
- **Prompt Skill**：自定义文本型 Skill，选择性挂载到不同智能体。**可执行插件尚未开放**。
- **费用约束**：同一条消息最多让 4 位成员各回复一次，防止无限自动对话；演示模式不调用 API。
- **本地默认安全**：默认只监听 `127.0.0.1`。非环回地址必须提供访问口令，并应经过 HTTPS/VPN。

## 快速开始：Windows / Linux

需要 **Python 3.10+**。在解压后的项目目录运行：

```bash
python -m pip install -e .
python -m agent_team.server
```

然后在浏览器访问 **http://127.0.0.1:8765**。

Windows 可运行 `scripts/start-windows.ps1`；Linux 可运行 `bash scripts/start-linux.sh`。首次启动会生成三个演示角色，功能可立即试用，但这些不是实际 AI 回复。

**想真正聊天**：打开「⚙ 模型与凭据」，添加 API Key，进入私聊并点「编辑智能体」，选择该凭据并填写提供商真实的模型 ID。之后发送消息即可。请留意各模型的费用、可用区域和接口限制。

## Android 手机端

**方式 A：手机本机运行服务**——理论上可在装有 Python 的 Android Linux 环境（例如 Termux）运行本服务，然后用 Android 浏览器访问 `http://127.0.0.1:8765`。Termux 不一定具备所有 Python 依赖的现成安装包，因此此方式尚未实机验证。

**方式 B：共用桌面/服务器上的机器人**——把项目服务部署在电脑或可信服务器，再通过 HTTPS 域名访问，手机与桌面共享聊天记录、Agent 设置及模型凭据。不要直接把无认证的 Python 服务暴露到公网。

局域网测试时可设置访问口令并选择监听地址：

```bash
# Linux
export AGENT_TEAM_ACCESS_TOKEN='替换为随机长口令'
python -m agent_team.server --host 0.0.0.0 --port 8765
```

```powershell
# Windows PowerShell
$env:AGENT_TEAM_ACCESS_TOKEN='替换为随机长口令'
python -m agent_team.server --host 0.0.0.0 --port 8765
```

**注意：以上只是受信任网络下的试验配置。** 使用手机跨网络连接时，必须在服务前加入 HTTPS 和相应的网络访问控制（例如 Tailscale / 自建 VPN / HTTPS 反向代理），不能在公网用纯 HTTP 传送访问口令。

## 工作方式

1. 点击左侧智能体直接开启私聊；或点「新建聊天 / 群组」选择多个智能体。
2. 选择模式：`@点名`（默认仅首位回复，最省钱）、`圆桌`（最多四位依次回复）、`协作`（最多四位结合前文接力完善答案）。
3. 群聊中输入 `@小码 帮我检查小研的方案` 可指定目标。一个智能体可以在多个群中出现。
4. 在「Skills」创建文本型任务指令，再进入「编辑智能体」挂载。

**模型使用方式**：

| 服务商 | 当前接入 | 说明 |
|---|---|---|
| OpenAI | API Key | Chat Completions 文本接口 |
| DeepSeek | API Key | 兼容 Chat Completions |
| OpenRouter | API Key | 经统一接口连接第三方模型，实际可用模型依赖账户及渠道 |
| Anthropic Claude | API Key | 原生 Messages API |
| Google Gemini | API Key | 原生 generateContent API |
| Ollama | 本地 HTTP，无 Key 可用 | 本地运行在**服务端所在设备**，不一定是浏览器所在的手机 |
| 其他兼容接口 | API Key + Base URL | 需要兼容 Chat Completions，部分差异仍需专用适配 |
| 厂商账户 OAuth 登录 | **未实现** | 仅在厂商提供公开/合法授权流程时接入，不爬取浏览器 Cookie |

ChatGPT Plus/Pro 等聊天订阅通常不等同于 API 配额；API 可能另外计费。

## 项目结构

```text
agent-team/
├── src/agent_team/
│   ├── server.py             # FastAPI HTTP / 静态资源
│   ├── storage.py            # SQLite、本地凭据加密、会话数据
│   ├── chat_service.py       # 模型适配器、@点名、群聊编排
│   ├── core.py               # 保留早期 CLI 任务协作引擎
│   ├── provider.py           # 保留早期 CLI API 适配器
│   └── web/                  # 自适应 PWA（HTML/CSS/JS）
├── docs/                     # 产品规格、架构、安全与路线图
├── tests/                    # 27 项离线测试（含旧 CLI）
├── scripts/                  # 启动脚本
├── AGENTS.md                 # Codex/编码智能体协作规范
└── pyproject.toml
```

## 自动化测试

```bash
python -m pip install -e '.[test]'
python -m unittest discover -s tests -v
```

或者用 `pytest -q`。在不配置真实 API Key 的情况下，测试不会连接外部 LLM。

## 安全边界

- 这是**单人自托管原型**，尚无多租户身份隔离、权限精细化、操作系统原生 Keychain 集成和生产级审计。
- 存储目录为 `~/.agent-team`（可由 `AGENT_TEAM_DATA_DIR` 指定）。备份聊天记录时，若需保留密钥解密能力，必须同时安全备份 `agent-team.sqlite3` 和 `encryption.key`；切勿将它们提交 GitHub。
- `API Key` 会从服务端发送到相应模型服务商；聊天内容也会发送给该模型服务商。
- Skill 当前只有文本指令：它**没有**执行 shell、访问浏览器、打开本地文件等能力。可执行插件需要显式授权和隔离执行层。
- Web 客户端目前不具备原生离线 AI 推理能力；离线 PWA 只能打开缓存页面，不能在服务端关闭时继续聊天。
- 具体的需求边界和下一阶段计划见 [产品规格](docs/PRODUCT_SPEC.md)、[架构](docs/ARCHITECTURE.md)、[安全说明](docs/SECURITY.md)、[路线图](docs/ROADMAP.md)。

## GitHub

GitHub 仓库：https://github.com/pjc011227-glitch/agent-team 。

### 导入 Skill 文本

在「Skills」面板可以从本地 `SKILL.md` 或 `.txt` 文件导入**纯文本指令**，编辑检查后保存，再分配给相应的智能体。导入操作不会安装依赖、运行脚本或执行文件内的命令。
