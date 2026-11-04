# 架构设计（v0.3）

```text
Windows / Linux 浏览器/PWA    Android 浏览器/PWA
                  \             /
                HTTPS/VPN/loopback
                       |
                  FastAPI Server
              /           |        \
          SQLite      Chat engine   Static PWA
             |           |
    encrypted credentials     |-- @ 点名路由
                              |-- 圆桌发言（有界）
                              |-- 协作接力（共享上下文）
                              |-- Provider adapters
                                   |-- OpenAI-compatible（OpenAI、DeepSeek、OpenRouter 等）
                                   |-- Anthropic Messages
                                   |-- Google Gemini generateContent
                                   `-- Ollama-compatible local
```

## 当前数据模型

- `credentials`: 名称、服务商、base URL、加密 API Key。密钥以 Fernet 加密，Web API 仅返回 `has_key`。
- `agents`: 名称、备注、头像、人格、模型 ID、credential ID、Skill IDs。
- `skills`: 指令型 Skill 的名称、说明、内容。
- `conversations`: 私聊/群聊、名称、执行模式。
- `conversation_agents`: 多对多成员关系，保存成员发言顺序。
- `messages`: 用户/Agent 消息、消息作者快照和时间戳。

## 关键选择

1. **集中式单人服务端**而非三个独立 App 的重复逻辑。未来客户端可使用相同的 REST/WebSocket 协议。
2. **先 Web/PWA，后原生壳**。可以快速验证手机/桌面交互；真正的 Windows、Linux、Android 安装包计划以 Tauri 2 引入，是否需要本地后端 Sidecar 另做决策。
3. **服务商接入采用 adapter**，每个 Agent 可指向不同的 Credential 与 Model。
4. **有界协作**。串行最多四次调用，下一 Agent 在上下文中可看到之前 Agent 的回复；非多进程、不承诺并行执行或后台自我迭代。
5. **文本型 Skill 与可执行 Plugin 严格区分**。当前 Skill 只是被拼接到 Agent 的系统提示词，没有工具执行权。

## 下一代架构计划

- 客户端：Tauri 2 原生外壳、Android 后台生命周期、统一 UI 状态管理。
- 服务端：任务队列、流式 WebSocket/SSE、取消/重试、消息编辑/历史版本、准确 token/成本统计。
- Plugin 层：签名 manifest、逐 Agent 权限、MCP 连接、工具执行审计与用户审批；不信任第三方插件。
- 存储：审计事件、数据库迁移、credential rotation、备份恢复、手机与桌面同步冲突策略。
- 安全：OAuth（仅官方支持的供应商）、密钥平台化、安全存储、端到端安全模型、细粒度访问控制。

## Skill / Plugin 扩展接口（规划阶段）

`Prompt Skill` 的第一阶段格式是 `name`、`description`、`instructions`。用户可以手动创建或从本地 `SKILL.md` / `.txt` 导入文字。系统**不会执行**导入文本中描述的命令或脚本。

可执行 Plugin 在正式实现前至少必须提供以下清单和机制：

```json
{
  "id": "example.readonly-search",
  "version": "0.1.0",
  "display_name": "示例只读搜索",
  "runtime": "mcp",
  "requested_permissions": ["network:example.com"],
  "requires_user_approval": true
}
```

以上是**设计示例，不是已经存在的插件安装格式**。后续应加入隔离进程/容器、权限白名单、版本/来源验证、运行审计、撤销授权和逐次确认敏感操作。


## 远程设备任务（v0.3）

参见 [远程控制](REMOTE_CONTROL.md)；新增 `remote_devices`、`remote_tasks`，设备 Worker 使用与用户令牌分离的一次性设备密钥，通过 HTTPS 轮询单设备队列；服务器负责跨手机/电脑聊天数据同步与任务状态持久化。当前未支持任意 GUI 工具调用。
