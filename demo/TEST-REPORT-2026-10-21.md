# Demo 全流程实测报告（2026-10-21）

> 环境：Windows / Git Bash / Python 3.15.0 / pyjwt / cryptography
> 实验目录：`f:\临时文件夹\升级\demo\`
> 报告生成：优化提交 `f022404` 之后，清空 `demo/` 运行时目录（`release`、`data`、`app`、`clientdata`、`backups`、`logs`）从零重跑，全部 8 步走通，无异常。

---

## 一、执行结果总览

| 步骤 | 命令 / 动作 | 结果 |
|---|---|---|
| 1 | `init-keys` | ✅ 生成 `demo/data/keys/{priv,pub}.pem` |
| 2 | 填充发布目录（v1 内容） | ✅ `demo/release/` 含 `app.py` + `config.json` |
| 3 | `server`（后台） | ✅ `/healthz` 返回 `{"status": "ok"}` |
| 4 | `publish --version v1` | ✅ 2 文件签名，清单持久化 |
| 5 | `token` + `sync`（v1） | ✅ `added: 2`，验签通过，全量备份 `v0` |
| 6 | 更新发布目录为 v2 + `publish --version v2` | ✅ 2 文件签名 |
| 7 | `sync`（v2 增量） | ✅ `updated: 2`，本地内容变 v2，全量备份 `v1`（`meta.json: {"version": "v1", "full": true}`） |
| 8 | `rollback --version v1` | ✅ `restored: 2, deleted: 0, snapshot_restored: true, healthy: true`，内容与快照均回到 v1 |

附加验证：

| 新特性 | 验证方式 | 结果 |
|---|---|---|
| 版本历史自然排序 | 发布 `v9`/`v10`/`v1` 后调 `/api/v1/history` | ✅ 返回 `["v10", "v9", "v2", "v1"]`（旧字符串排序会是 `["v9","v2","v10","v1"]`） |
| JWT 密钥自动生成 | 未配置 `jwt_secret` 的 `ServerConfig.resolve_jwt_secret()` | ✅ 生成 64 字符密钥，持久化到 `data_dir/jwt_secret.key`，同目录二次解析结果一致（跨进程稳定） |
| fail-closed 验签 | `client.json` 配 `verify_sign: true` + 公钥路径 | ✅ 日志 `loaded public key for signature verification`，同步通过 |
| 全量备份 | 备份 `meta.json` | ✅ `{"version": "v1", "full": true}`，回滚时按镜像复原目录 |

---

## 二、分步命令与输出

### Step 1 · 生成密钥对

```bash
python -m upgrader.cli init-keys --config demo/server.json
```

```
{"key_dir": "f:\\临时文件夹\\升级\\demo\\data\\keys", "public_pem": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAGAlVfQe3tfPMBqlLX..."}
```

### Step 2 · 填充发布目录（v1 内容）

```bash
rm -rf demo/release demo/data demo/app demo/clientdata demo/backups demo/logs
mkdir -p demo/release
cp -r demo/release_v1/. demo/release/
```

`demo/release_v1/config.json` 内容：`{"feature_flag": false, "message": "v1"}`。

### Step 3 · 启动服务端（后台）

```bash
nohup python -m upgrader.cli server --config demo/server.json > /dev/null 2>&1 &
curl -s http://127.0.0.1:8000/healthz
# => {"status": "ok"}
```

### Step 4 · 发布 v1

```bash
python -m upgrader.cli publish --config demo/server.json --version v1
```

```
{"version": "v1", "published_at": "2026-10-21T13:16:43Z", "files": 2, "sign": "joOgkBT5StqlP6rUBG0bMWT3TEljSWrQwwu5NaVWcKHMECmJCqiwJcLl3mRlyRGFzxhclD6WBs6O3oIllgkfBA=="}
```

### Step 5 · 签发 token 并同步 v1

```bash
TOKEN=$(python -m upgrader.cli token --config demo/server.json --role client --subject demo-app --ttl 7200)
# 将 $TOKEN 写入 demo/client.json 的 token 字段
python -m upgrader.cli sync --config demo/client.json
```

```
21:17:01 [INFO] upgrader.client :: loaded public key for signature verification
21:17:01 [INFO] upgrader.client :: backup done root=...demo\backups\v0 files=0/0 full=True
21:17:01 [INFO] upgrader.client :: applied version=v1 +2 ~0 -0 unchanged=0
21:17:01 [INFO] upgrader.client :: sync done version=v1 added=2 updated=0 deleted=0 unchanged=0 in 0.11s
{"version": "v1", "added": 2, "updated": 0, "deleted": 0, "unchanged": 0, "ok": true, "rolled_back": false, "error": ""}
```

首次同步无旧 snapshot，备份命名为 `v0`，目标目录尚空故 0 文件（全量标记仍为 `full=True`）。

### Step 6 · 发布 v2（更新同一发布目录）

```bash
rm -f demo/release/*
cp -r demo/release_v2/. demo/release/
python -m upgrader.cli publish --config demo/server.json --version v2
```

```
{"version": "v2", "published_at": "2026-10-21T13:17:18Z", "files": 2, "sign": "SLLqzHazXrGORPg0SvnWZe4GRT+5BPg4D8s2nuKVZzH1hgIKZAYv0YA5UsrKVyZdWYc9XvZ3xZ+NvPQOBDg=="}
```

### Step 7 · 增量同步 v2 并验证

```bash
python -m upgrader.cli sync --config demo/client.json
```

```
21:17:19 [INFO] upgrader.client :: backup done root=...demo\backups\v1 files=2/2 full=True
21:17:19 [INFO] upgrader.client :: applied version=v2 +0 ~2 -0 unchanged=0
{"version": "v2", "added": 0, "updated": 2, "deleted": 0, "unchanged": 0, "ok": true, "rolled_back": false, "error": ""}
```

验证：

```bash
cat demo/app/app.py
# => print("hello from app v2")
python -m upgrader.cli version --config demo/client.json
# => {"local_version": "v2"}
ls demo/backups
# => v0  v1
```

### Step 8 · 回滚到 v1

```bash
cat demo/backups/v1/meta.json
# => {"version": "v1", "full": true}
python -m upgrader.cli rollback --config demo/client.json --version v1
```

```
{"restored": 2, "deleted": 0, "snapshot_restored": true, "backup_root": "f:\\临时文件夹\\升级\\demo\\backups\\v1", "healthy": true}
```

回滚后验证：

```bash
cat demo/app/app.py
# => print("hello from app v1")
cat demo/app/config.json
# => {"feature_flag": false, "message": "v1"}
python -m upgrader.cli version --config demo/client.json
# => {"local_version": "v1"}
```

### 附加：历史自然排序

```bash
python -m upgrader.cli publish --config demo/server.json --version v9
python -m upgrader.cli publish --config demo/server.json --version v10
python -m upgrader.cli publish --config demo/server.json --version v1
curl -s -H "Authorization: Bearer <client token>" http://127.0.0.1:8000/api/v1/history
# => {"history": ["v10", "v9", "v2", "v1"]}
```

数字段按数值比较，`v10` 正确排在 `v9` 前。

### 附加：JWT 密钥自动生成

```python
from upgrader.server.config import ServerConfig
cfg = ServerConfig(data_dir="/tmp/srv")   # 未配置 jwt_secret（默认占位符）
s = cfg.resolve_jwt_secret()
# len(s) == 64；持久化到 /tmp/srv/jwt_secret.key
# 同 data_dir 二次解析结果一致（跨进程稳定）
```

---

## 三、与旧流程记录（TEST-README.md）的差异说明

以下为**预期增强**，非异常：

1. 回滚输出多了 `deleted` 与 `healthy` 字段：回滚现在会清理备份镜像之外的多余文件，并执行健康检查（结果记在 `healthy`，失败仅告警）。
2. 每个备份目录多了 `meta.json`（`{"version", "full"}`）：区分全量 / 增量备份，全量备份回滚时按镜像复原整个目录。
3. `client.json` 的 `public_key` 字段现在同时支持 **PEM 文件路径**与 **内联 PEM 内容**（本 demo 仍用路径形式）。
4. 服务端 `jwt_secret` 未配置或为占位符时**自动生成并持久化**，不再要求手工准备 ≥32 字节密钥。

---

## 四、清理说明

重跑前执行的清理命令：

```bash
rm -rf demo/release demo/data demo/app demo/clientdata demo/backups demo/logs
```

`demo/release_v1`、`demo/release_v2`（源内容）与两个配置文件保留。报告生成后后台服务进程已停止。
