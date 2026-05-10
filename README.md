# V2RayAggregator

自动化的代理节点聚合、测速、筛选工具。

## 功能

- 从多个公开来源采集代理节点（SS/SSR/VMess/Trojan/Vless/Hysteria2）
- 通过 subconverter 转换节点格式
- 使用 singtools 进行节点测速
- 按速度排序筛选优质节点
- 输出多种格式（Clash YAML / Base64 / Mixed）
- 自动更新订阅链接（日期递增、GitHub Release 检测等）
- Windows / Linux 双平台兼容

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 准备工具

首次运行会自动下载 subconverter 和 singtools 到 tools/ 目录。
也可手动下载放置：

- subconverter: https://github.com/asdlokj1qpi233/subconverter/releases
- singtools: https://github.com/Kdwkakcs/singtools/releases

### 运行

```bash
# 完整流程（采集→测速→筛选→输出）
python scripts/run.py all

# 仅采集
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

## 项目结构

```
V2RayAggregator/
├── config/                 # 配置文件
│   ├── settings.yaml       # 全局设置
│   ├── sub_sources.json    # 订阅源配置
│   ├── singtools_config.json  # 测速配置
│   └── clash_template.yaml # Clash 配置模板
├── core/                   # 核心模块
│   ├── collector.py        # 节点采集
│   ├── converter.py        # 格式转换
│   ├── merger.py           # 合并与去重
│   ├── namer.py            # GeoIP 命名
│   ├── speedtester.py      # 测速执行
│   ├── filter.py           # 筛选排序
│   ├── formatter.py        # 输出格式化
│   └── source_updater.py   # 订阅源更新
├── tools/                  # 本地工具
│   ├── subconverter/       # subconverter
│   └── singtools/          # singtools
├── scripts/                # 运行脚本
│   └── run.py              # CLI 入口
├── output/                 # 输出目录
└── .cache/                 # 运行时缓存（Country.mmdb）
```

## License

MIT
