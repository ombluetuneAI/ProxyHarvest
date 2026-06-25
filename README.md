# ProxyHarvest

自动化的代理节点采集、测速、筛选工具。

## 功能

- 从多个公开来源采集代理节点（SS/VMess/Trojan/VLESS/Hysteria2）
- 通过 MetaCubeX/subconverter 转换节点格式
- 使用 Mihomo 筛选存活节点
- 使用 singtools 对存活节点测速并排序
- 输出 Clash YAML 配置（`nodes_clash.yaml`）
- 自动更新订阅链接（日期递增、GitHub Release 检测等）
- Windows / Linux 双平台兼容
- GitHub Actions 自动定时运行

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 准备工具

首次运行需要 subconverter、singtools 和 mihomo：

- **subconverter** (MetaCubeX v0.9.2): https://github.com/MetaCubeX/subconverter/releases
- **singtools** (vv0.2.0): https://github.com/Kdwkakcs/singtools/releases
- **mihomo** (v1.19.27): https://github.com/MetaCubeX/mihomo/releases

Windows 用户可运行 `scripts/setup.ps1` 自动下载上述工具。

Linux 用户：将 subconverter 解压到 `tools/subconverter/linux64/`，singtools 解压到 `tools/singtools/linux/`，mihomo 解压到 `tools/mihomo/linux/`。

> GitHub Actions 工作流会自动下载这些工具，无需手动操作。

### 运行

```bash
# 完整流程（采集 → Mihomo 筛存活 → 测速排序 → 输出 nodes_clash.yaml）
python scripts/run.py all

# 独立 Mihomo 验证（供 clash_gen 等场景使用）
python scripts/run.py clash-validate --input output/clash_merge.yaml --output output/clash_all.yaml
```

## 流水线说明

`run.py all` 执行三步：

1. **采集**：从订阅源拉取节点，去重，GeoIP 重命名
2. **Mihomo 筛选**：连通性检测，仅保留存活节点
3. **singtools 测速**：对存活节点测带宽并排序（不剔除 speed=0 的节点），写入 `output/nodes_clash.yaml`

## 输出文件

| 文件 | 说明 | 来源 |
|------|------|------|
| `output/nodes_clash.yaml` | Collector 产出的 Clash 配置 | `collector.yml` |
| `output/clash_all.yaml` | anaer/Sub 合并验证后的 Clash 配置 | `clash_gen.yml` |
| `output/clash.yaml` | 保留待用 | 手动 |

## 订阅链接

推送仓库到 GitHub 后，可用以下 Raw URL 作为订阅链接：

```
https://raw.githubusercontent.com/<用户名>/ProxyHarvest/main/output/nodes_clash.yaml
```

## 项目结构

```
ProxyHarvest/
├── config/                     # 配置文件
│   ├── settings.yaml           # 全局设置
│   ├── sub_sources.json        # 订阅源配置
│   ├── singtools_config.json   # 测速配置
│   └── clash_template.yaml     # Clash 配置模板
├── core/                       # 核心模块
│   ├── collector.py            # 节点采集
│   ├── converter.py            # 格式转换（subconverter API）
│   ├── merger.py               # 合并与去重
│   ├── namer.py                # GeoIP 命名
│   ├── speedtester.py          # 测速执行
│   ├── clash_validator.py      # Mihomo 节点验证
│   ├── source_updater.py       # 订阅源更新
│   ├── geoip.py                # GeoIP 数据库
│   └── platform_utils.py       # 平台工具函数
├── tools/                      # 本地工具（gitignored）
├── scripts/
│   ├── run.py                  # CLI 入口
│   └── generate_clash.py       # clash_gen 合并脚本
├── output/                     # 输出目录
└── .github/workflows/
    ├── collector.yml           # 采集流水线
    └── clash_gen.yml           # anaer/Sub 合并流水线
```

## GitHub Actions

- **collector.yml**：每 4 小时运行，输出 `nodes_clash.yaml`
- **clash_gen.yml**：每 8 小时运行，输出 `clash_all.yaml`
- 均支持手动触发（`force_push` 可在无变更时打空提交）

## License

MIT
