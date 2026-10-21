# AutoUpgrader 自动升级工具

面向 **Docker 容器环境** 的通用自动升级工具。不限程序类型/语言（前端、后端、桌面、Web 等），
客户端与服务端通过 **增量哈希同步** 对比文件差异并拉取更新，同时提供
**JWT 安全认证、Ed25519 清单签名、备份回滚、健康检查、完整性校验** 等能力。

- 传输方式：HTTP 拉取式（客户端主动拉取，避免服务端直连/暴露）
- 最小依赖：仅 `pyjwt` + `cryptography`，HTTP 层使用 Python 标准库
- Python 版本：3.15.0（兼容 3.10+）

---

## 一、总体架构

```
┌────────────────────────┐         ┌────────────────────────┐
│      发布管理端         │         │      业务容器（客户端）  │
│  (admin)               │         │                        │
│                        │         │  ┌────────────────────┐ │
│  1. 放置新版文件到     │         │  │  SyncEngine        │ │
│     publish_dir        │  ───▶  │  │  拉取/校验/替换/回滚│ │
│  2. upgrader publish   │  HTTP  │  └────────────────────┘ │
│     (生成签名清单)      │  拉取  │  钩子 pre / post replace │
└────────────────────────┘         │  健康检查（命令 / URL） │
                                   └────────────────────────┘
```

核心流程：

1. **发布端**：把新版本文件放入 `publish_dir`，执行 `upgrader publish`，
   服务端扫描文件树、计算 `sha256 + size + mtime` 清单，用 Ed25519 对清单整体签名，
   持久化并设为当前版本。
2. **客户端**：定时（cron / CI / 一次性任务）执行 `upgrader sync`：
   - 拉取当前版本清单并**验签**（`verify_sign` 开启时）；
   - 与本地 snapshot 做 **diff**（to_add / to_update / to_delete）；
   - **备份**受影响文件到 `backup_dir/<version>/`；
   - 执行 `hooks_pre_replace`；
   - 下载差异文件到临时目录，校验 sha256，**原子 rename** 替换；
   - 删除多余文件，写新 snapshot；
   - 执行 `hooks_post_replace`；
   - 运行**健康检查**（命令或 URL）；失败则**自动回滚**（恢复文件 + 旧 snapshot + 钩子）。

### 目录结构

```
升级/
├── upgrader/
│   ├── cli.py               # 统一 CLI 入口
│   ├── logcfg.py            # 日志（级别 / 轮转 / 去重）
│   ├── crypto.py            # Ed25519 签名 + JWT
│   ├── manifest.py          # 清单 / diff / 快照
│   ├── server/              # 服务端（HTTP / 认证 / 版本管理）
│   │   ├── app.py
│   │   ├── config.py
│   │   └── version_store.py
│   └── client/              # 客户端（同步 / 校验 / 备份 / 健康检查）
│       ├── sync.py
│       ├── verify.py
│       ├── backup.py
│       ├── health.py
│       ├── rollback.py
│       ├── httpclient.py
│       └── config.py
├── tests/                   # 离线 pytest 测试（不依赖外部工具）
├── docker/                  # Dockerfile / compose 示例
└── pyproject.toml
```

---

## 二、安装

### 本地 / 源码安装

```bash
cd 根目录
pip install .              # 安装 upgrader 命令
pip install pyjwt cryptography   # 如需单独使用依赖
```

安装后可直接使用 `upgrader` 命令，或以模块方式运行：

```bash
python -m upgrader.cli <子命令>
```

### 运行测试

```bash
pip install pytest
python -m pytest -q        # 46 用例，离线验证
```

---

## 三、服务端

### 3.1 配置（`ServerConfig`）

配置通过 JSON 文件加载（`--config`），或用环境变量 `UPGRADER_CONFIG` 指向。
最小配置：

```json
{
  "host": "0.0.0.0",
  "port": 8000,
  "publish_dir": "/srv/releases",
  "data_dir": "/var/lib/upgrader",
  "jwt_secret": "请换成至少32位的随机字符串",
  "admin_jwt_secret": "管理面独立密钥（可选，缺省复用 jwt_secret）",
  "log_level": "INFO",
  "log_file": "/var/log/upgrader/server.log"
}
```

完整字段：

| 字段 | 说明 | 默认 |
|---|---|---|
| `host` / `port` | HTTP 监听地址 | `0.0.0.0` / `8000` |
| `publish_dir` | 发布目录（被签名文件树） | `./release` |
| `data_dir` | 数据目录（版本历史 / 清单 / 密钥） | `./data` |
| `key_dir` | Ed25519 密钥目录，缺省 `data_dir/keys` | — |
| `jwt_secret` | 数据面 JWT 密钥（建议 ≥32 字节） | — |
| `admin_jwt_secret` | 管理面 JWT 密钥，缺省复用 `jwt_secret` | — |
| `log_level` | 日志级别 `DEBUG/INFO/WARNING/...` | `INFO` |
| `log_file` | 日志文件路径（留空仅控制台） | — |
| `max_bytes` / `backup_count` | 日志轮转大小 / 保留份数 | `10485760` / `5` |
| `include_patterns` / `exclude_patterns` | 清单扫描包含/排除过滤（子串匹配，详见 3.7 节） | — |
| `token_ttl` | 签发 token 有效期（秒） | `3600` |

### 3.7 文件过滤：`include_patterns` / `exclude_patterns`

`build_manifest` 按相对路径做**子串匹配**（不是 glob）：

```python
rel = "dist/js/main.js"          # 文件相对 publish_dir 的路径
if include_patterns and not any(p in rel for p in include_patterns):
    continue                     # 配置了 include 时，不在白名单内的文件被跳过
if any(p in rel for p in exclude_patterns):
    continue                     # 黑名单优先，命中即跳过
```

| 行为 | 说明 |
|---|---|
| 不配置 `include_patterns` | 全量同步发布目录下的所有文件 |
| 配置了 `include_patterns` | **白名单**：只同步相对路径包含其中任一子串的文件 |
| 配置了 `exclude_patterns` | **黑名单**：命中即跳过，优先级高于 include |

**示例**（`server.json`）：

```json
{
  "include_patterns": ["dist/", "app/"],
  "exclude_patterns": [".map", ".log", "test_"]
}
```

- `dist/` 匹配 `dist/js/main.js`（带 `/` 前缀可精确到目录层级）
- 排除所有 `.map`、`.log` 文件，以及相对路径中含 `test_` 的文件（如 `tests/test_x.py`、`scripts/test_run.sh`）

> **注意**：子串匹配意味着 `app` 也会匹配到 `myapp/x.js`。要精确控制目录层级，请带上前缀字符，例如 `myapp/`。

> **密钥长度**：Python 3.15 下 PyJWT 短密钥（<32 字节）由警告升级为异常，
> 生产环境请务必使用 ≥32 字节密钥（可用 `python -c "import secrets;print(secrets.token_urlsafe(32))"` 生成）。

### 3.2 生成密钥对

```bash
upgrader init-keys --config server.json
```

在 `key_dir` 生成 `priv.pem`（私钥）与 `pub.pem`（公钥）。客户端验签需将
`pub.pem` 内容配置到客户端 `public_key` 字段。

### 3.3 启动服务端

```bash
upgrader server --config server.json
```

### 3.4 发布新版本

```bash
# 指定版本号
upgrader publish --config server.json --version v2.1.0

# 不指定则自动按时间戳生成
upgrader publish --config server.json
```

发布流程：扫描 `publish_dir` → 计算每文件 `sha256/size/mtime` 清单 →
Ed25519 签名 → 持久化到 `data_dir/manifests/<version>.json` → 设为当前版本。

### 3.5 签发 JWT Token

```bash
# 数据面（客户端拉取用）
upgrader token --config server.json --role client --subject app1 --ttl 7200

# 管理面（发布用）
upgrader token --config server.json --role admin --subject ops --ttl 3600
```

输出为标准 JWT 字符串，配置到客户端 `token` 字段（数据面）或用于发布请求（管理面）。

### 3.6 HTTP API

认证方式：请求头 `Authorization: Bearer <JWT>`。

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/healthz` | 无 | 存活探针 |
| GET | `/api/v1/manifest` | client | 当前版本清单（含 `sign`） |
| GET | `/api/v1/version` | client | 当前版本信息 |
| GET | `/api/v1/files?rel=<path>` | client | 下载单文件（防目录穿越） |
| GET | `/api/v1/history` | client | 版本历史 |
| POST | `/api/v1/publish` | admin | 发布新版本 |
| POST | `/api/v1/token` | client | 客户端 token 校验 |

---

## 四、客户端

### 4.1 配置（`ClientConfig`）

```json
{
  "server_url": "http://upgrader-server:8000",
  "token": "<client 角色 JWT>",
  "target_dir": "/app",
  "snapshot_dir": "/var/lib/upgrader",
  "backup_dir": "/var/lib/upgrader/backups",
  "public_key": "-----BEGIN PUBLIC KEY-----\n...",
  "verify_sign": true,
  "health_command": "python /app/healthcheck.py",
  "health_timeout": 30,
  "health_retries": 3,
  "health_retry_interval": 2,
  "hooks_pre_replace": ["stop_service.sh"],
  "hooks_post_replace": ["start_service.sh"],
  "retries": 2,
  "log_level": "INFO",
  "log_file": "/var/log/upgrader/client.log"
}
```

完整字段：

| 字段 | 说明 |
|---|---|
| `server_url` | 服务端基础 URL |
| `token` | 客户端 JWT（client 角色） |
| `target_dir` | 被同步目录 |
| `snapshot_dir` | 本地快照目录（记录已同步版本） |
| `backup_dir` | 备份目录（回滚用） |
| `public_key` | Ed25519 公钥 PEM（`verify_sign` 开启时必填） |
| `verify_sign` | 是否验签，默认 `true` |
| `health_command` / `health_url` | 健康检查：外部命令（返回码）或 HTTP URL（2xx） |
| `health_timeout` | 健康检查超时（秒） |
| `health_retries` | 健康检查失败后**重试次数**（默认 `0` 只查一次） |
| `health_retry_interval` | 两次健康检查间隔（秒，默认 `2`） |
| `hooks_pre_replace` / `hooks_post_replace` | 替换前 / 后执行的钩子命令列表 |
| `batch_size` | 每次同步拉取文件批大小 |
| `retries` | 单文件下载重试次数 |
| `log_level` / `log_file` / `max_bytes` / `backup_count` | 日志配置 |

### 4.2 同步

```bash
upgrader sync --config client.json
```

同步结果输出 JSON：`{"ok": true, "version": "v2.1.0", "added": 3, "updated": 2, "deleted": 1, ...}`。
健康检查失败会**自动回滚**并返回 `"ok": false, "rolled_back": true`。

**健康检查重试（容忍服务启动慢）**：

`hooks_post_replace` 启动的服务往往需要数秒才监听就绪。若 `health_retries` 为 0（默认），
健康检查在钩子返回瞬间探测，服务尚未就绪会误判失败并自动回滚。配置示例：

```json
{
  "health_retries": 5,
  "health_retry_interval": 2
}
```

表示失败后每隔 2s 重试，最多 5 次，给服务留出约 10s 的就绪窗口。
仍失败才判定为不健康并触发回滚。

### 4.3 回滚

```bash
# 查看可用备份版本（在 backup_dir 下列出）
upgrader version --config client.json

# 回滚到指定备份版本
upgrader rollback --config client.json --version v2.0.0
```

### 4.4 查看本地版本

```bash
upgrader version --config client.json
# 输出 {"local_version": "v2.1.0"}
```

---

## 五、Docker 部署

### 5.1 构建镜像

服务端：

```bash
docker build -f docker/Dockerfile.server -t upgrader-server .
```

客户端（业务容器内运行同步，执行一次退出，适合 cron / CI / k8s Job）：

```bash
docker build -f docker/Dockerfile.client -t upgrader-client .
```

基础镜像均为 `python:3.15-slim`，只安装 `pyjwt` 与 `cryptography`。

### 5.2 compose 示例

参考 `docker/compose.example.yaml`，组合升级中心、业务容器与客户端侧车：

```yaml
services:
  upgrader-server:
    build: { context: ., dockerfile: docker/Dockerfile.server }
    ports: ["8000:8000"]
    volumes:
      - ./releases:/srv/releases        # 发布目录
      - server-data:/var/lib/upgrader   # 清单/密钥
      - ./server.json:/etc/upgrader/config.json

  my-app:
    image: your-business-image:latest
    volumes: ["app-files:/app"]

  upgrader-client:
    build: { context: ., dockerfile: docker/Dockerfile.client }
    command: ["sync", "--config", "/etc/upgrader/client.json"]
    volumes:
      - app-files:/app                  # 同步到业务目录
      - client-data:/var/lib/upgrader
      - ./client.json:/etc/upgrader/client.json
    depends_on: [upgrader-server]

volumes:
  server-data: {}
  client-data: {}
  app-files: {}
```

### 5.3 端口暴露安全

客户端采用 **主动拉取** 模式，服务端只需对客户端可达，建议：

- 生产环境在 `upgrader-server` 前加 **TLS 终结**（Nginx / 负载均衡 / k8s Ingress），
  用 HTTPS 防止中间人篡改 manifest / 文件内容；
- 限制 `jwt_secret` / `admin_jwt_secret` 的访问权限，管理面（`admin` 角色）
  尽量内网隔离；
- 容器内不直接暴露 8000 到公网，用 compose 的 `expose` 而非 `ports`。

---

## 六、日志系统

`upgrader.logcfg.setup_logger()` 提供统一的日志配置：

- **级别可控**：`log_level` 字段（`DEBUG/INFO/WARNING/ERROR/CRITICAL`）；
- **双输出**：控制台 + 滚动文件（`RotatingFileHandler`，默认 10MB × 5 份）；
- **去重**：同名 logger 不会重复挂 handler；
- 结构化字段：关键操作（发布、同步、回滚、健康检查、认证失败）均带上下文日志，
  便于排查认证 401/403、哈希不匹配、签名不匹配等问题。

---

## 七、安全机制

| 机制 | 说明 |
|---|---|
| **JWT 认证** | 数据面 `client` 角色、管理面 `admin` 角色隔离，双密钥面 |
| **Ed25519 清单签名** | 对 `files + version + published_at` 整体签名，防 manifest 篡改 |
| **哈希校验** | 每个文件 sha256 校验，下载后校验，不匹配重试并最终报错 |
| **防目录穿越** | 文件下载端点对 `rel` 做 `normpath` 校验，拒绝 `..` 与绝对路径 |
| **备份 + 回滚** | 每次同步前备份，健康检查失败自动回滚到旧 snapshot |
| **原子替换** | 下载到临时目录后 `os.replace`，避免半写入 |

---

## 八、测试

`tests/` 下 46 个 pytest 用例，**全部离线验证，不依赖外部工具/网络**：

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `test_crypto.py` | 7 | Ed25519 密钥/签名/篡改拒绝、JWT 签发/验证/错误密钥/过期 |
| `test_manifest.py` | 7 | 清单扫描/过滤/序列化/diff/快照 |
| `test_server_http.py` | 8 | 真实 `ThreadingHTTPServer` 随机端口：认证 401/403、manifest/版本/下载/历史/目录穿越 |
| `test_sync.py` | 4 | 全量同步、noop、哈希损坏重试、签名不匹配拒绝（`FakeTransport` 离线模拟） |
| `test_backup.py` | 4 | 备份/恢复/回滚 API/版本列表 |
| `test_health.py` | 6 | 命令/URL 健康检查（monkeypatch 隔离） |
| `test_logcfg.py` | 4 | 级别过滤/轮转/去重/全级别 |
| `test_cli.py` | 4 | CLI 参数/token/init-keys/version |
| `test_e2e.py` | 2 | 端到端：健康检查失败自动回滚、版本不变无操作 |

```bash
python -m pytest -q     # 46 passed
```

---

## 九、快速上手（最小示例）

```bash
# 1. 准备发布目录
mkdir -p release data
echo "hello v1" > release/app.txt

# 2. 初始化密钥
upgrader init-keys --config server.json

# 3. 启动服务端
upgrader server --config server.json &

# 4. 发布 v1
upgrader publish --config server.json --version v1

# 5. 签发 client token
TOKEN=$(upgrader token --config server.json --role client --subject app1)

# 6. 客户端同步（client.json 配置 server_url / token / target_dir / public_key / verify_sign）
upgrader sync --config client.json
```

---

## 十、版本与许可

- 当前版本：`1.0.0`
- 依赖：`pyjwt>=2.8.0`、`cryptography>=42.0.0`
- Python：`>=3.10`（3.15.0 已验证，含 PEP 709 闭包行为与 PyJWT 短密钥异常兼容修复）
