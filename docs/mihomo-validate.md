# 使用独立 Mihomo 内核验证节点有效性

本文介绍如何用 ProxyHarvest 拉起一个**独立的 Mihomo（Clash.Meta）内核**，对 Clash YAML 中的代理节点做**连通性 / 延迟**检测。

> 适用场景：本地或 CI 环境快速判断节点是否还能连上。
> 不适用：带宽测速（请用 `singtools`，见 `python scripts/run.py speedtest`）。

---

## 原理简述

```
ProxyHarvest                         独立 mihomo 进程
─────────────                        ─────────────────
读取 clash.yaml ─启动内核(-d/-f)─▶  mihomo (独立端口 127.0.0.1:9091)
       │                                  │
       │                             批量延迟测试
       │                             (/proxies/{name}/delay)
       ▼                                  ▼
输出 JSON/CSV 报告 ◀── REST API ──  每个节点的 delay (ms)
       │
       └─ 测完自动 kill 掉 mihomo 进程
```

1. 解析 `mihomo` 二进制路径与配置（端口、超时、测速 URL 等）。
2. 把待测节点写入临时配置 `output/tmp/mihomo/proxyharvest_validate.yaml`。
3. 用 `mihomo -d <data_dir> -f <config>` 启动独立内核，等待 API 就绪。
4. 逐节点并发测延迟（失败时最多重试 3 次）。
5. 关闭 mihomo 进程，生成报告到 `output/tmp/`。

检测标准：对 `http://www.gstatic.com/generate_204`（可配置）发起探测；**延迟 > 0 ms 视为有效**。

---

## 前置条件

| 条件 | 说明 |
|------|------|
| `mihomo` 二进制已就位 | `tools/mihomo/windows/mihomo.exe` 或 `tools/mihomo/linux/mihomo` |
| Python 依赖已安装 | `pip install -r requirements.txt` |
| 待测文件为 Clash 格式 YAML | 含 `proxies:` 列表 |
| 能正常访问外网 | 内核需真实拨号到测速 URL |

### 安装 mihomo 二进制

**Windows**：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

**手动**：从 https://github.com/MetaCubeX/mihomo/releases 下载，放到 `tools/mihomo/` 对应目录。

---

## 快速开始

```bash
python scripts/run.py clash-validate
python scripts/run.py clash-validate --input output/clash.yaml
```

---

## 配置文件

`config/settings.yaml` 的 `mihomo` 段：

```yaml
mihomo:
  input: output/clash.yaml
  controller_port: 9091
  mixed_port: 7899
  test_url: "http://www.gstatic.com/generate_204"
  timeout_ms: 10000
  max_retries: 3
  use_group_test: false
  max_workers: 32
```

---

## 输出文件

| 文件 | 说明 |
|------|------|
| `output/tmp/clash_validate_report.json` | 完整报告 |
| `output/tmp/clash_validate_summary.csv` | 按订阅源汇总 |

---

## 相关代码

| 模块 | 职责 |
|------|------|
| `core/mihomo_manager.py` | 启停独立 mihomo 进程、生成验证配置 |
| `core/mihomo_client.py` | Mihomo REST HTTP 客户端 |
| `core/clash_validator.py` | 执行检测、生成报告 |
| `scripts/run.py` | `clash-validate` 命令入口 |
