# AutoUpgrader Windows 实测步骤

> 环境：Windows / PowerShell / Python 3.15.0 / pyjwt 2.15.1 / cryptography 50.0.2
> 实验目录：`f:\临时文件夹\升级\demo\`
> 本步骤已在 2026-10-21 实测通过（v1→v2 增量升级 + 回滚）。

---

## 一、实验目录结构

```
demo/
├── release_v1/            # 模拟 v1 版本发布内容
│   ├── app.py
│   └── config.json
├── release_v2/            # 模拟 v2 版本发布内容
│   ├── app.py
│   └── config.json
├── release/               # 服务端发布目录（实际被扫描签名）
├── data/                  # 服务端数据目录（清单 / 密钥）
│   └── keys/
│       ├── priv.pem       # Ed25519 私钥
│       └── pub.pem        # Ed25519 公钥（客户端验签用）
├── app/                   # 客户端目标目录（被同步/升级）
├── clientdata/            # 客户端本地快照
├── backups/               # 客户端备份（回滚用）
├── logs/
│   ├── server.log
│   └── client.log
├── server.json            # 服务端配置
└── client.json            # 客户端配置
```

### 配置文件内容

**`demo/server.json`**

```json
{
  "host": "127.0.0.1",
  "port": 8000,
  "publish_dir": "f:\\临时文件夹\\升级\\demo\\release",
  "data_dir": "f:\\临时文件夹\\升级\\demo\\data",
  "jwt_secret": "windows-demo-jwt-secret-00000000000",
  "admin_jwt_secret": "windows-demo-admin-secret-00000000",
  "log_level": "INFO",
  "log_file": "f:\\临时文件夹\\升级\\demo\\logs\\server.log",
  "token_ttl": 3600
}
```

**`demo/client.json`**

```json
{
  "server_url": "http://127.0.0.1:8000",
  "token": "<签发后的 client JWT>",
  "target_dir": "f:\\临时文件夹\\升级\\demo\\app",
  "snapshot_dir": "f:\\临时文件夹\\升级\\demo\\clientdata",
  "backup_dir": "f:\\临时文件夹\\升级\\demo\\backups",
  "public_key": "f:\\临时文件夹\\升级\\demo\\data\\keys\\pub.pem",
  "verify_sign": true,
  "health_command": "",
  "retries": 2,
  "log_level": "INFO",
  "log_file": "f:\\临时文件夹\\升级\\demo\\logs\\client.log"
}
```

> 注意：`client.json` 的 `public_key` 是**公钥文件路径**（不是 PEM 内容本身）。

---

## 二、完整操作步骤

所有命令均在项目根目录 `f:\临时文件夹\升级` 下执行（PowerShell）。

### Step 1 · 生成 Ed25519 密钥对

```powershell
python -m upgrader.cli init-keys --config demo\server.json
```

输出示例：

```
{"key_dir": "f:\\...\\demo\\data\\keys", "public_pem": "-----BEGIN PUBLIC KEY-----\nMCow..."}
```

在 `demo\data\keys\` 下生成 `priv.pem` 与 `pub.pem`。

### Step 2 · 填充发布目录（v1 内容）

```powershell
New-Item -ItemType Directory -Force -Path demo\release | Out-Null
Copy-Item demo\release_v1\* demo\release\ -Recurse
```

### Step 3 · 启动服务端（后台）

```powershell
python -m upgrader.cli server --config demo\server.json
```

看到日志：

```
upgrader server listening on 127.0.0.1:8000 publish_dir=f:\...\demo\release
server started pid=xxxxx
```

> 服务需保持运行；PowerShell 中可用 `Start-Job` 或在另一终端窗口运行。

### Step 4 · 发布 v1

```powershell
python -m upgrader.cli publish --config demo\server.json --version v1
```

输出示例：

```
{"version": "v1", "published_at": "2026-...Z", "files": 2, "sign": "LlBl..."}
```

### Step 5 · 签发 client token 并同步 v1

```powershell
# 签发 token（输出到控制台，需手动填入 client.json 的 token 字段）
python -m upgrader.cli token --config demo\server.json --role client --subject demo-app --ttl 7200
```

将输出的 JWT 替换 `demo\client.json` 中的 `"token": "..."`。

```powershell
# 同步 v1
python -m upgrader.cli sync --config demo\client.json
```

输出示例：

```
{"version": "v1", "added": 2, "updated": 0, "deleted": 0, "unchanged": 0, "ok": true, "rolled_back": false, "error": ""}
```

验证 `demo\app\` 下已有 v1 文件：

```powershell
Get-Content demo\app\app.py
# => print("hello from app v1")
```

### Step 6 · 发布 v2（关键：更新发布目录内容）

> **这是最关键的一步**。`run_server` 启动时把 `publish_dir` 固化进运行进程，
> 因此发新版时必须**把新文件更新进同一个发布目录**（模拟真实发布流程），
> 而不是切换 `publish_dir`。

```powershell
# 把 v2 文件覆盖进发布目录
Copy-Item demo\release_v2\* demo\release\ -Recurse -Force

# 发布 v2
python -m upgrader.cli publish --config demo\server.json --version v2
```

### Step 7 · 客户端增量同步 v2

```powershell
python -m upgrader.cli sync --config demo\client.json
```

输出示例：

```
{"version": "v2", "added": 0, "updated": 2, "deleted": 0, "unchanged": 0, "ok": true, "rolled_back": false, "error": ""}
```

验证 `demo\app\` 已更新为 v2：

```powershell
Get-Content demo\app\app.py
# => print("hello from app v2")
python -m upgrader.cli version --config demo\client.json
# => {"local_version": "v2"}
```

### Step 8 · 回滚到 v1（可选）

```powershell
python -m upgrader.cli rollback --config demo\client.json --version v1
```

输出示例：

```
{"restored": 0, "snapshot_restored": true, "backup_root": "f:\\...\\backups\\v1"}
```

> 回滚语义：恢复"同步到 v2 之前"的 v1 备份状态 + 还原本地快照为 v1。

---

## 三、实测结果记录

| 步骤 | 命令 | 输出 | 结果 |
|---|---|---|---|
| 同步 v1 | `sync` | `{"version":"v1","added":2,"ok":true}` | ✅ 文件落到 `demo\app\`，备份到 `backups\v0\`（首次无旧版本） |
| 同步 v2 | `sync` | `{"version":"v2","updated":2,"ok":true}` | ✅ 增量更新为 v2 内容，**备份到 `backups\v1\`（修复后按旧版本命名）** |
| 查版本 | `version` | `{"local_version":"v2"}` | ✅ |
| 回滚 v1 | `rollback --version v1` | `{"restored":2,"snapshot_restored":true,"backup_root":"...\backups\v1"}` | ✅ `restored:2`，文件内容回到 v1，快照回 v1 |

---

## 四、踩坑与注意事项

1. **发布目录必须固定**
   `run_server` 启动时把 `publish_dir` 固化进运行进程内存。`publish` 是独立 CLI 子进程，
   它扫描的目录必须与运行中服务一致。**发新版 = 把新文件更新进同一个发布目录**，
   不是切换目录。第一次实测就踩了这个坑：把 `server.json` 的 `publish_dir` 改成
   `release_v2` 再 publish，运行中的服务仍指向 `release_v1`，导致客户端下载
   `app.py` 拿到旧内容，哈希与 v2 清单不符 → 自动报错（完整性校验机制生效）。

2. **备份目录按"被备份内容的版本"命名（已修复）**
   早期实现里 `sync` 把备份命名为目标版本（同步到 v2 时把 v1 旧文件备份到
   `backups/v2/`），导致 `rollback --version v1` 找不到 `backups/v1/` 而失败。
   修复后 `sync.py` 改为用 `snap.current_version()`（当前本地版本）作为备份 key，
   即同步到 v2 前把 v1 旧文件备份到 `backups/v1/`，回滚 `--version v1` 即可命中。
   首次同步（无旧 snapshot）时 `current_version()` 为空，回退到 `v0`。

2. **token 需对应当前密钥**
   `client.json` 的 `token` 必须用当前 `server.json` 的 `jwt_secret` 签发。
   若重新 `init-keys` 或修改密钥，需重新签发 token 并更新 `client.json`。

3. **验签依赖公钥路径**
   `client.json` 的 `public_key` 字段是**公钥文件路径**（如 `demo\data\keys\pub.pem`），
   不是 PEM 字符串。`verify_sign: true` 时该路径必须存在且可读。

4. **JWT 密钥长度（Python 3.15）**
   Python 3.15 下 PyJWT 对 <32 字节密钥的 `InsecureKeyLengthWarning` 升级为异常。
   本 demo 的 `jwt_secret` / `admin_jwt_secret` 已 ≥32 字节，生产环境请用随机密钥：
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`。

5. **回滚语义**
   每次同步前自动备份"同步前的文件状态"到 `backup_dir/<version>/`。
   `rollback --version v1` 恢复该备份目录内容并把本地快照还原为 v1。
   `restored: 0` 表示备份里的内容和本地当前一致（v2 同步前备份的就是 v1 文件）。

6. **清理重跑**
   想从零重跑可先清理：
   ```powershell
   Remove-Item -Recurse -Force demo\release, demo\data, demo\app, demo\clientdata, demo\backups, demo\logs -ErrorAction SilentlyContinue
   ```
   然后从 Step 1 重新执行。
