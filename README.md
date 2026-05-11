# V2RayAggregator

自动化的代理节点聚合、测速、筛选工具。

## 功能

- 从多个公开来源采集代理节点（SS/VMess/Trojan/VLESS/Hysteria2）
- 通过 MetaCubeX/subconverter 转换节点格式
- 使用 singtools 进行节点测速
- 按速度排序筛选优质节点
- 输出多种格式（Clash YAML / Base64 / Mixed / 协议拆分）
- 自动更新订阅链接（日期递增、GitHub Release 检测等）
- Windows / Linux 双平台兼容
- GitHub Actions 自动定时运行

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 准备工具

首次运行需要 subconverter 和 singtools：

- **subconverter** (MetaCubeX v0.9.2): https://github.com/MetaCubeX/subconverter/releases
- **singtools** (vv0.2.0): https://github.com/Kdwkakcs/singtools/releases

Windows 用户：将 subconverter 解压到 `tools/subconverter/windows/`，singtools 解压到 `tools/singtools/windows/`。

Linux 用户：将 subconverter 解压到 `tools/subconverter/linux64/`，singtools 解压到 `tools/singtools/linux/`。

> GitHub Actions 工作流会自动下载这些工具，无需手动操作。

### 运行

```bash
# 完整流程（采集→测速→筛选→输出）
python scripts/run.py all

# 仅采集 + 格式化输出（跳过测速）
python scripts/run.py collect

# 仅测速
python scripts/run.py speedtest

# 仅格式化输出
python scripts/run.py format

# 更新订阅源
python scripts/run.py update-sources

# 更新 GeoIP 数据库
python scripts/run.py update-geoip
```

## 输出文件

运行后，`output/` 目录下会生成以下文件：

| 文件 | 说明 |
|------|------|
| `nodes_clash.yaml` | Clash 配置（含代理组和规则模板） |
| `nodes_base64.txt` | Base64 编码订阅（通用格式） |
| `nodes_mixed.txt` | 混合格式订阅（每行一个 URI） |
| `sub/splitted/nodes_ss.txt` | SS 协议节点（Base64） |
| `sub/splitted/nodes_vmess.txt` | VMess 协议节点（Base64） |
| `sub/splitted/nodes_trojan.txt` | Trojan 协议节点（Base64） |
| `sub/splitted/nodes_vless.txt` | VLESS 协议节点（Base64） |
| `sub/splitted/nodes_hysteria2.txt` | Hysteria2 协议节点（Base64） |

## 订阅链接

推送仓库到 GitHub 后，可用以下 Raw URL 作为订阅链接：

```
https://raw.githubusercontent.com/<用户名>/V2RayAggregator/main/output/nodes_clash.yaml
https://raw.githubusercontent.com/<用户名>/V2RayAggregator/main/output/nodes_base64.txt
https://raw.githubusercontent.com/<用户名>/V2RayAggregator/main/output/nodes_mixed.txt
```

## 项目结构

```
V2RayAggregator/
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
│   ├── filter.py               # 筛选排序
│   ├── formatter.py            # 输出格式化
│   ├── source_updater.py       # 订阅源更新
│   ├── geoip.py                # GeoIP 数据库
│   ├── constants.py            # 共享常量
│   ├── config_loader.py        # 配置加载
│   └── platform_utils.py       # 平台工具函数
├── tools/                      # 本地工具（gitignored）
│   ├── subconverter/           # subconverter 二进制
│   └── singtools/              # singtools 二进制
├── scripts/
│   └── run.py                  # CLI 入口
├── output/                     # 输出目录
├── .cache/                     # 运行时缓存（Country.mmdb）
└── .github/workflows/
    └── collector.yml           # GitHub Actions 工作流
```

## GitHub Actions

工作流每天 10:00 和 22:00 (CST) 自动运行，也可手动触发：

- **定时运行**: `0 2,14 * * *` UTC
- **手动触发**: 支持跳过测速 (`skip_speedtest`) 和强制推送 (`force_push`)
- **Push 触发**: config/core/scripts/requirements 变更时

## 订阅源

| # | 来源 | 方法 | 说明 |
|---|------|------|------|
| 0 | pojiezhiyuanjun/freev2 | change_date | GitHub 每日更新 |
| 1 | free-nodes/v2rayfree | change_date | GitHub 每日更新 |
| 2 | Pawdroid/Free-servers | auto | GitHub 静态链接 |
| 3 | nodefree.org | change_date | 需境外网络 |
| 4 | v2rayshare.com | change_date | 需境外网络 |
| 5 | clashnode.com | change_date | 需境外网络 |

## License

MIT
