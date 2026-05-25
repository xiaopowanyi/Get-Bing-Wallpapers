# 获取 Bing 每日壁纸

自动抓取 Bing 每日壁纸（UHD 超高清），写入 EXIF 元数据，并通过 GitHub Actions 定时运行、自动归档到指定仓库。

## 功能特性

- 每日自动获取 Bing 中国区壁纸（UHD 分辨率）
- 自动写入 EXIF 信息（标题、描述、版权）
- 智能查重：本地 + 远端双重检测，避免重复下载
- 自动更新目标仓库 README 中的最新壁纸橱窗
- 支持 Bark 消息推送通知
- GitHub Actions 定时任务，每日自动执行
- 失败自动重试机制

## 项目结构

```
├── getbingpic.py          # 主脚本
├── requirements.txt       # Python 依赖
├── .github/workflows/
│   └── python.yml         # GitHub Actions 工作流
└── README.md
```

## 快速开始

### 本地运行

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 运行脚本：

```bash
python getbingpic.py
```

图片默认保存在当前目录下的 `Basics/` 和 `Add Exif/` 文件夹中，按 `年/月` 归档。

### GitHub Actions 自动运行

工作流每天 UTC 时间 02:30（北京时间 10:30）自动执行，也支持手动触发。

#### 需要配置的 Secrets

| Secret 名称 | 说明 | 必填 |
|---|---|---|
| `WALLPAPER_REPO` | 壁纸存储仓库，格式 `owner/repo` | 是 |
| `TARGET_REPO_TOKEN` | 目标仓库的 Personal Access Token | 是 |
| `PUSH_TYPE` | 推送渠道，目前支持 `bark` | 否 |
| `BARK_URL` | Bark 服务地址 | 否 |
| `BARK_KEY` | Bark 设备 Key | 否 |

#### 如何获取 TARGET_REPO_TOKEN

`TARGET_REPO_TOKEN` 是一个 GitHub Personal Access Token (PAT)，用于让 Actions 有权限向壁纸存储仓库推送代码。获取步骤：

1. 登录 GitHub，点击右上角头像 → **Settings**
2. 左侧菜单滚动到底部，点击 **Developer settings**
3. 选择 **Personal access tokens** → **Fine-grained tokens**（推荐）或 **Tokens (classic)**
4. 点击 **Generate new token**

**Fine-grained token（推荐）：**
- Token name：填写一个便于识别的名称，如 `bing-wallpaper-push`
- Expiration：设置过期时间（建议不超过 1 年，到期后需重新生成）
- Resource owner：选择壁纸仓库所属的账号/组织
- Repository access：选择 **Only select repositories**，选中壁纸存储仓库
- Permissions → Repository permissions：
  - **Contents**：设为 `Read and write`（用于推送图片和更新 README）
- 点击 **Generate token**，复制生成的 Token

**Classic token：**
- 勾选 `repo` 权限（完整的仓库读写权限）
- 点击 **Generate token**，复制生成的 Token

5. 回到本项目仓库，进入 **Settings** → **Secrets and variables** → **Actions**
6. 点击 **New repository secret**，名称填 `TARGET_REPO_TOKEN`，值粘贴刚才复制的 Token

> ⚠️ Token 只在生成时显示一次，请妥善保存。如果丢失需要重新生成。

## 环境变量

| 变量名 | 说明 | 默认值 |
|---|---|---|
| `OUTPUT_DIR` | 图片输出根目录 | `.`（当前目录） |
| `WALLPAPER_REPO` | GitHub 壁纸仓库路径 | - |
| `GITHUB_TOKEN` / `TARGET_REPO_TOKEN` | GitHub API Token | - |
| `PUSH_TYPE` | 推送类型 | `bark` |
| `BARK_URL` | Bark 推送服务地址 | - |
| `BARK_KEY` | Bark 设备 Key | - |

## 输出目录结构

```
output_data/
├── Basics/                # 原始壁纸
│   └── 2025/
│       └── 05/
│           └── 20250525_标题.jpg
├── Add Exif/              # 含 EXIF 信息的壁纸
│   └── 2025/
│       └── 05/
│           └── 20250525_标题.jpg
└── README.md              # 自动更新的壁纸橱窗
```

## 依赖

- Python 3.10+
- [requests](https://pypi.org/project/requests/) — HTTP 请求
- [Pillow](https://pypi.org/project/Pillow/) — 图像处理
- [piexif](https://pypi.org/project/piexif/) — EXIF 元数据读写

## License

MIT
