# ProxyHarvest

自动化的代理节点采集、测速、筛选与 Clash 配置生成工具。

项目包含两条独立流水线：

| 流水线 | 入口 | 产出 | CI 工作流 |
|--------|------|------|-----------|
| **Collector** | `run.py all` | `output/nodes_clash.yaml` | `collector.yml` |
| **Clash Gen** | `generate_clash.py` + `clash-validate` | `output/clash_all.yaml` | `clash_gen.yml` |

## 功能

- 从多个公开订阅源采集节点（SS / VMess / Trojan / VLESS / Hysteria2 等）
- 通过 MetaCubeX/subconverter 转换节点格式
- 使用独立 Mihomo 进程做连通性检测，筛除不可用节点
- 使用 singtools 对存活节点测速并排序
- 合并 [anaer/Sub](https://github.com/anaer/Sub) 远程节点与本地 `clash_all.yaml`，经 Mihomo 验证后更新配置
- 自动更新订阅链接（日期递增、GitHub Release 检测等）
- Windows / Linux 双平台兼容

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 准备工具

首次本地运行需要 subconverter、singtools 和 mihomo：

| 工具 | 版本 | 路径 |
|------|------|------|
| subconverter | v0.9.2 | `tools/subconverter/{linux64\|windows}/` |
| singtools | vv0.2.0 | `tools/singtools/{linux\|windows}/` |
| mihomo | v1.19.27 | `tools/mihomo/{linux\|windows}/` |

Windows 可运行 `scripts/setup.ps1` 自动下载上述工具及 Python 依赖。

Linux 手动解压到对应目录即可。GitHub Actions 工作流会自动下载，无需手动操作。

### 运行

```bash
# Collector 完整流程：采集 → Mihomo 筛存活 → 测速排序 → nodes_clash.yaml
python scripts/run.py all

# Clash Gen：拉取 anaer/Sub 并与本地 clash_all 合并
python scripts/generate_clash.py

# Mihomo 节点验证（Clash Gen 第 2 步，也可单独使用）
python scripts/run.py clash-validate --input output/clash_merge.yaml --output output/clash_all.yaml
```

## 流水线说明

### Collector（`run.py all`）

1. **更新订阅源**：自动刷新 `config/sub_sources.json` 中的链接
2. **采集**：从各订阅源拉取节点，按 server/port/type 去重
3. **GeoIP 重命名**：按国家与 IP 生成统一节点名
4. **Mihomo 筛选**：连通性检测，仅保留存活节点
5. **singtools 测速**：按带宽（或 ping）排序，写入 `output/nodes_clash.yaml`

采集阶段会额外写出中间文件 `output/nodes_clash_merge.yaml`（含 `_source_id`，供调试，不提交 Git）。

### Clash Gen（`generate_clash.py` + `clash-validate`）

1. **合并**：从 `anaer/Sub` 拉取 `proxies.yaml`，与仓库内 `output/clash_all.yaml` 合并为 `output/clash_merge.yaml`
   - 相同节点（server/port/type 等）以远程为准
   - 名称冲突时 secondary 节点自动重命名为 `原名#2`、`原名#3` …
2. **验证**：Mihomo 逐节点测延迟，仅保留存活节点，写入 `output/clash_all.yaml`

## 输出文件

| 文件 | 说明 | 提交 Git | 来源 |
|------|------|----------|------|
| `output/nodes_clash.yaml` | Collector 最终 Clash 配置 | 是 | `collector.yml` |
| `output/nodes_clash_merge.yaml` | 采集后、筛选前的中间配置 | 否 | `run.py all` |
| `output/clash_merge.yaml` | 远程 + 本地合并后的中间配置 | 否 | `generate_clash.py` |
| `output/clash_all.yaml` | Clash Gen 验证后的 Clash 配置 | 是 | `clash_gen.yml` |
| `output/clash.yaml` | 手动验证时的默认输出 | 是 | `clash-validate`（无 `--output` 时） |
| `output/tmp/` | Mihomo / 测速等临时文件 | 否 | 运行时生成 |

## 订阅链接

推送仓库到 GitHub 后，可用 Raw URL 作为 Clash 订阅：

```
# Collector 产出
https://raw.githubusercontent.com/<用户名>/ProxyHarvest/main/output/nodes_clash.yaml

# Clash Gen 产出
https://raw.githubusercontent.com/<用户名>/ProxyHarvest/main/output/clash_all.yaml
```

## 项目结构

```
ProxyHarvest/
├── config/
│   ├── settings.yaml           # 全局设置（端口、超时、工具路径等）
│   ├── sub_sources.json        # 订阅源配置
│   ├── singtools_config.json   # 测速配置
│   └── clash_template.yaml     # Clash 配置模板
├── core/
│   ├── collector.py            # 节点采集
│   ├── converter.py            # 格式转换（subconverter API）
│   ├── merger.py               # 合并与去重
│   ├── namer.py                # GeoIP 命名
│   ├── speedtester.py          # singtools 测速
│   ├── clash_validator.py      # Mihomo 节点验证
│   ├── mihomo_manager.py       # Mihomo 进程管理
│   ├── mihomo_client.py        # Mihomo REST API 客户端
│   ├── source_updater.py       # 订阅源自动更新
│   ├── config_loader.py        # 配置加载
│   ├── geoip.py                # GeoIP 数据库
│   └── platform_utils.py       # 跨平台工具函数
├── docs/
│   └── mihomo-validate.md        # Mihomo 验证原理与用法
├── scripts/
│   ├── run.py                  # CLI 入口（all / clash-validate）
│   ├── generate_clash.py       # Clash Gen 合并脚本
│   └── setup.ps1               # Windows 环境初始化
├── tools/                      # 本地二进制（gitignored）
├── output/                     # 输出目录
└── .github/workflows/
    ├── collector.yml           # Collector 流水线
    └── clash_gen.yml           # Clash Gen 流水线
```

## GitHub Actions

| 工作流 | 触发方式 | 定时 | 提交文件 |
|--------|----------|------|----------|
| `collector.yml` | 手动 / 定时 | 每天 06:00（Asia/Shanghai） | `nodes_clash.yaml`、`sub_sources.json` |
| `clash_gen.yml` | 手动 / 定时 | 每天 04:00（Asia/Shanghai） | `clash_all.yaml` |

两条流水线均支持 `workflow_dispatch` 手动触发。运行失败或调试时可从 Actions Artifacts 下载 `output/` 目录（含 `clash_merge.yaml`、Mihomo 日志等）。

## 更多文档

- [Mihomo 节点验证说明](docs/mihomo-validate.md)

## License

MIT
