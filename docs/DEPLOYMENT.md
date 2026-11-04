# Agent Team Hub：自托管部署（v0.3.3）

> 提醒：目前是**单人测试用的服务端**，不是可直接放到公网使用的成熟多用户产品。服务器端仍需配合 HTTPS、账户凭据保护、访问限速和备份。桌面 Agent Worker 不需要独立公网 IP。

## 推荐网络拓扑

```text
手机 Android/PWA ───┐
                  ├── TLS / VPN ── Hub (Docker + SQLite) <── 出站 HTTPS ── Windows/Linux Worker
桌面 Web/PWA ──────┘         :8765 (只绑定服务器 loopback)
```

服务器只保存智能体聊天、设备任务及加密后的模型凭据；不会直接运行电脑 GUI。电脑端使用独立设备令牌登录，令牌撤销后立即失效。

## Docker Compose 部署（在你信任的云服务器）

安装 Docker Engine 与 Compose V2 后，在服务器执行：

```bash
git clone https://github.com/pjc011227-glitch/agent-team.git
cd agent-team
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

复制上条命令生成的随机字符串，写入仓库根目录一个**新建的** `.env` 文件：

```env
AGENT_TEAM_ACCESS_TOKEN=这里换成上一条命令生成的随机字符串
```

执行：

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8765/api/status
```

默认只映射 `127.0.0.1:8765`（Docker 宿主机本地），**不会**把裸 HTTP 服务开放到公网。要让手机跨网络访问，需要自行配置 HTTPS 反向代理或私有 VPN。

### HTTPS 反向代理示例（宿主机上已有 Caddy）

在正确设置域名 DNS、开放 80/443、并运行支持自动证书的 Caddy 之后，可使用以下示例站点配置：

```caddyfile
agent.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8765
}
```

将 `agent.example.com` 替换为你实际控制的域名。**不要**将服务器的 8765 端口直接放通至公网。反向代理不等于完整的安全审计，建议进一步限制访问源、配置限流并更新依赖。

### 电脑连接 Hub

手机访问 `https://你的域名/remote.html`，输入上面配置的所有者口令，创建桌面设备并复制设备令牌。电脑端安装项目并在本地终端设置：

```bash
export AGENT_TEAM_HUB_URL='https://你的域名'
export AGENT_TEAM_DEVICE_TOKEN='从设备页面得到的一次性令牌'
agent-team-worker --workspace "$HOME/AgentTeamWorkspace"
```

Windows PowerShell 使用 `$env:AGENT_TEAM_HUB_URL='...'`、`$env:AGENT_TEAM_DEVICE_TOKEN='...'`，再运行 `agent-team-worker --workspace "C:\\AgentTeamWorkspace"`。

电脑仅能在用户授权目录执行白名单任务，写文件必须另外加 `--allow-write`。详细限制请看 [远程控制](REMOTE_CONTROL.md)。

### 数据与更新

- 聊天记录、设备与 API Key 加密数据存储在 Docker 命名卷 `agentteam_data`，不是在 Git 仓库中；数据库密钥 `/data/encryption.key` 与 SQLite 文件必须一起备份。
- 更新：在服务器 git pull 之后执行 `docker compose up -d --build`，升级前备份卷数据。
- 不要把 `.env`、数据库、设备配对令牌或 `encryption.key` 上传到 GitHub。
- 服务器断线会使聊天和任务暂时不可用；当前没有高可用主从或端到端加密。

## 目前不支持

- 通过手机直接执行任意电脑软件、远程桌面或 Shell；当前仅支持受控 Worker 白名单。
- 原生 Android APK 或 Windows/Linux 原生安装包：目前仍需要 Python Worker 和浏览器/PWA。
- 多人共享 Hub、复杂权限和完善审计；请勿在不可信网络中裸露服务。
