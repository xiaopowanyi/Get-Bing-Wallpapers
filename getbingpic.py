# -*- coding: utf-8 -*-
import argparse
import functools
import os
import re
import shutil
import sys
import time
from typing import List, Optional, Tuple

import piexif
from PIL import Image
import requests

# 优化跨平台控制台 UTF-8 输出，防止 Windows 控制台在输出中文或版权符 © 时抛出 UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# 默认全局 User-Agent
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
)


def retry(times: int = 3, delay: float = 2.0):
    """通用异常重试装饰器"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(times):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"[{func.__name__}] 第 {i + 1}/{times} 次执行失败: {e}")
                    if i < times - 1:
                        time.sleep(delay)
            print(f"[{func.__name__}] 达到最大重试次数 ({times})，已放弃。")
            return None
        return wrapper
    return decorator


class HttpClient:
    """全局复用 HTTP Session，优化连接池与超时"""
    _session: Optional[requests.Session] = None

    @classmethod
    def get_session(cls) -> requests.Session:
        if cls._session is None:
            cls._session = requests.Session()
            cls._session.headers.update({
                'User-Agent': DEFAULT_USER_AGENT
            })
        return cls._session


class BingAPI:
    """Bing 每日壁纸接口"""
    BASE_URL = 'https://cn.bing.com'
    TIMEOUT = 12

    @staticmethod
    @retry(times=3, delay=2.0)
    def fetch_wallpaper(market: str = 'zh-CN', idx: int = 0) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        获取 Bing 壁纸信息
        返回 (日期, 图片URL, 标题, 描述, 版权) 或全 None
        """
        url = f"{BingAPI.BASE_URL}/HPImageArchive.aspx?format=js&idx={idx}&n=1&mkt={market}"
        session = HttpClient.get_session()
        try:
            res = session.get(url, timeout=BingAPI.TIMEOUT)
            res.raise_for_status()
            res.encoding = 'utf-8'
            data = res.json().get('images', [])

            if not data:
                print("Bing 接口未返回壁纸数据。")
                return None, None, None, None, None

            item = data[0]
            pic_url = BingAPI.BASE_URL + item['urlbase'] + '_UHD.jpg'
            today = item.get('enddate', '')
            raw_title = item.get('title', '').strip()

            # 从 copyright 字段中拆分出描述和版权信息
            copyright_text = item.get('copyright', '').strip()
            copyright_list = copyright_text.replace('(', '').replace(')', '').split('©')
            comment = copyright_list[0].strip() if len(copyright_list) > 0 else ''
            copy_right = copyright_list[1].strip() if len(copyright_list) > 1 else copyright_text

            # 若 title 为空，尝试从描述或 urlbase 中提取备用标题
            if not raw_title:
                if comment:
                    raw_title = re.split(r'[,，、]', comment)[0].strip()
                elif 'urlbase' in item:
                    base_id = item['urlbase'].split('id=')[-1].split('_')[0]
                    raw_title = base_id.replace('OHR.', '')
                else:
                    raw_title = 'BingWallpaper'

            clean_title = BingAPI._clean_filename(raw_title)
            return today, pic_url, clean_title, comment, copy_right

        except Exception as e:
            print(f"获取 Bing 壁纸信息异常: {e}")
            raise e

    @staticmethod
    def _clean_filename(words: str) -> str:
        """清洗文件名：移除特殊符号与非法字符，保留中英文、数字，空格转下划线"""
        cleaned = re.sub(r'[\x00-\x1f\\/:*?"<>|]', '', words)
        cleaned = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s_-]', '', cleaned)
        cleaned = re.sub(r'\s+', '_', cleaned).strip('_')
        return cleaned or 'Wallpaper'


class GitHubManager:
    """GitHub API 交互与远端文件缓存管理"""

    def __init__(self, repo: Optional[str] = None, token: Optional[str] = None):
        self.repo = repo or os.environ.get('WALLPAPER_REPO')
        self.token = token or os.environ.get('GITHUB_TOKEN') or os.environ.get('TARGET_REPO_TOKEN')
        self._remote_files: Optional[List[str]] = None

    def get_remote_files(self) -> List[str]:
        """获取远端图片文件列表，支持缓存，规避 API 频次限制"""
        if self._remote_files is not None:
            return self._remote_files

        self._remote_files = []
        if not self.repo:
            return self._remote_files

        api_url = f"https://api.github.com/repos/{self.repo}/git/trees/main?recursive=1"
        headers = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'
        }
        if self.token:
            headers['Authorization'] = f"Bearer {self.token}"

        print(f"正在从 GitHub API 拉取远端文件树 ({self.repo})...")
        try:
            res = self._fetch_tree_with_retry(api_url, headers)
            if res and res.status_code == 200:
                tree_data = res.json().get('tree', [])
                for item in tree_data:
                    path = item.get('path', '')
                    if path.startswith('Basics/') and path.endswith('.jpg'):
                        self._remote_files.append(path)
                print(f"成功拉取远端图片列表，共计 {len(self._remote_files)} 张。")
            else:
                status_code = res.status_code if res else 'Unknown'
                print(f"拉取远端图片列表失败，状态码: {status_code}")
        except Exception as e:
            print(f"请求 GitHub API 出现异常: {e}")

        return self._remote_files

    @retry(times=3, delay=2.0)
    def _fetch_tree_with_retry(self, url: str, headers: dict):
        session = HttpClient.get_session()
        res = session.get(url, headers=headers, timeout=12)
        res.raise_for_status()
        return res


class StorageManager:
    """图片存储管理（下载、路径组织、双重查重）"""
    DOWNLOAD_TIMEOUT = 30

    def __init__(self, github_mgr: GitHubManager, base_dir: Optional[str] = None):
        self.github_mgr = github_mgr
        self.base_dir = base_dir or os.environ.get('OUTPUT_DIR', '.')
        self.basics_dir = os.path.join(self.base_dir, 'Basics')
        self.exif_dir = os.path.join(self.base_dir, 'Add Exif')

    def download_image(self, date_str: str, pic_url: str, title: str) -> Optional[str]:
        """下载原图到 Basics 目录，已存在则跳过，成功返回保存路径"""
        year, month = date_str[:4], date_str[4:6]
        filename = f"{date_str}_{title}.jpg"

        # 本地与远端双重查重
        if self._is_image_exist(filename, year, month):
            print(f"[{filename}] 已存在，跳过下载。")
            return None

        target_dir = os.path.join(self.basics_dir, year, month)
        os.makedirs(target_dir, exist_ok=True)
        file_path = os.path.join(target_dir, filename)

        print(f"开始下载 UHD 原图: {filename}")
        try:
            content = self._download_request(pic_url)
            if not content:
                raise ValueError("下载内容为空")
        except Exception as e:
            print(f"下载失败: {e}")
            return None

        with open(file_path, 'wb') as f:
            f.write(content)

        print(f"[{filename}] 原图下载成功 ({len(content):,} bytes)！")
        return file_path

    @retry(times=3, delay=3.0)
    def _download_request(self, pic_url: str) -> bytes:
        session = HttpClient.get_session()
        res = session.get(pic_url, timeout=self.DOWNLOAD_TIMEOUT)
        res.raise_for_status()
        return res.content

    def _is_image_exist(self, filename: str, year: str, month: str) -> bool:
        """查重：优先检查本地路径，其次比对缓存的远端文件树"""
        # 1. 本地目录检查
        local_path = os.path.join(self.basics_dir, year, month, filename)
        if os.path.exists(local_path):
            return True

        # 2. 远端仓库文件树检查
        rel_path = f"Basics/{year}/{month}/{filename}"
        remote_files = self.github_mgr.get_remote_files()
        if rel_path in remote_files:
            return True

        return False

    def get_exif_path(self, date_str: str, title: str) -> str:
        """计算 EXIF 版本的图片存储路径"""
        year, month = date_str[:4], date_str[4:6]
        target_dir = os.path.join(self.exif_dir, year, month)
        os.makedirs(target_dir, exist_ok=True)
        return os.path.join(target_dir, f"{date_str}_{title}.jpg")


class ExifManager:
    """EXIF 元数据写入管理器（优先无损二进制注入，保障 UHD 画质）"""

    def __init__(self, storage: StorageManager):
        self.storage = storage

    def attach_exif(self, original_path: str, date_str: str, title: str, comment: str, copyright_text: str) -> str:
        """
        向原图注入 EXIF 信息并另存到 Add Exif 目录
        采用 piexif.insert 二进制段无缝注入，完全避免 Pillow 解码再编码产生的画质损失
        """
        save_path = self.storage.get_exif_path(date_str, title)

        if os.path.exists(save_path):
            print(f"[{os.path.basename(save_path)}] EXIF 版本已存在，跳过。")
            return save_path

        print("正在写入 EXIF 元数据...")

        # 构造 EXIF 数据字典
        try:
            exif_dict = piexif.load(original_path)
        except Exception:
            exif_dict = {}

        if not isinstance(exif_dict, dict):
            exif_dict = {}
        for section in ('0th', 'Exif', 'GPS', 'Interop', '1st'):
            if section not in exif_dict or not isinstance(exif_dict[section], dict):
                exif_dict[section] = {}

        # 注入标题、版权与描述
        exif_dict['0th'][piexif.ImageIFD.ImageDescription] = title.encode('utf-8')
        exif_dict['0th'][piexif.ImageIFD.Copyright] = copyright_text.encode('utf-8')
        exif_dict['0th'][piexif.ImageIFD.XPComment] = comment.encode('utf-16le')

        try:
            exif_bytes = piexif.dump(exif_dict)
            # 无损直接注入到 JPEG 原始文件流中，保留 100% 原始超清像素数据
            piexif.insert(exif_bytes, original_path, new_file=save_path)
            print("EXIF 无损注入完成。")
            return save_path
        except Exception as e:
            print(f"无损注入 EXIF 失败 ({e})，降级使用 Pillow 高画质模式保存...")

        # 降级备用方案：Pillow 最高质量重新保存
        try:
            with Image.open(original_path) as img:
                exif_bytes = piexif.dump(exif_dict)
                img.save(save_path, 'jpeg', exif=exif_bytes, quality=95, subsampling=0)
            print("EXIF (Pillow 兼容模式) 写入完成。")
            return save_path
        except Exception as err:
            print(f"EXIF 写入异常: {err}，直接拷贝原图。")
            shutil.copyfile(original_path, save_path)
            return save_path


class ReadmeGenerator:
    """README 动态图片橱窗生成器"""

    def __init__(self, storage: StorageManager):
        self.storage = storage
        self.readme_path = os.path.join(storage.base_dir, 'README.md')
        self.start_mark = "<!-- gallery_start -->"
        self.end_mark = "<!-- gallery_end -->"

    def _get_remote_pics(self) -> List[str]:
        return self.storage.github_mgr.get_remote_files()

    def _get_local_pics(self) -> List[str]:
        pics = []
        if not os.path.exists(self.storage.basics_dir):
            return pics
        for root, _, files in os.walk(self.storage.basics_dir):
            for file in files:
                if file.lower().endswith('.jpg'):
                    rel_path = os.path.relpath(os.path.join(root, file), self.storage.base_dir)
                    pics.append(rel_path.replace('\\', '/'))
        return pics

    def _get_all_pics(self) -> List[str]:
        all_pics = self._get_remote_pics() + self._get_local_pics()
        if not all_pics:
            return []
        return sorted(set(all_pics), reverse=True)

    def _get_latest_3_pics_md(self) -> str:
        all_pics = self._get_all_pics()
        if not all_pics:
            return f"{self.start_mark}\n{self.end_mark}"

        latest_pics = all_pics[:3]
        pic_infos = []
        for pic in latest_pics:
            basename = os.path.basename(pic)
            pic_title = basename.replace('.jpg', '')
            date_str = pic_title[:8]
            formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if len(date_str) == 8 else ""
            name = pic_title.split('_', 1)[1] if '_' in pic_title else pic_title
            pic_infos.append((formatted_date, name, pic))

        wallpaper_repo = self.storage.github_mgr.repo or ''
        raw_base = f"https://raw.githubusercontent.com/{wallpaper_repo}/refs/heads/main" if wallpaper_repo else ""

        gallery_md = "## 🌟 最新壁纸\n\n"
        gallery_md += "|" + "|".join(f"{d} {n}" for d, n, _ in pic_infos) + "|\n"
        gallery_md += "| " + " | ".join(":--------------------------------------------:" for _ in pic_infos) + " |\n"
        gallery_md += "| " + " | ".join(f'<img src="{p}" width="300">' for _, _, p in pic_infos) + " |\n"
        if raw_base:
            gallery_md += "|" + "|".join(f'**[UHD 原图下载]({raw_base}/{p})**' for _, _, p in pic_infos) + "|\n"
        gallery_md += "\n"

        return f"{self.start_mark}\n{gallery_md}{self.end_mark}"

    def get_latest_pic_url(self) -> Optional[str]:
        all_pics = self._get_all_pics()
        if not all_pics:
            return None
        latest = all_pics[0]
        wallpaper_repo = self.storage.github_mgr.repo or ''
        if wallpaper_repo:
            return f"https://raw.githubusercontent.com/{wallpaper_repo}/refs/heads/main/{latest}"
        return latest

    def update(self):
        """替换 README 中 gallery 标记区域的内容"""
        try:
            gallery_block = self._get_latest_3_pics_md()

            if os.path.exists(self.readme_path):
                with open(self.readme_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                if self.start_mark in content and self.end_mark in content:
                    content = re.sub(rf"{self.start_mark}.*?{self.end_mark}", gallery_block, content, flags=re.DOTALL)
                    with open(self.readme_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print("README.md 橱窗更新完成。")
                else:
                    print("README.md 未找到 gallery 标记，跳过更新。")
            else:
                print(f"README.md 路径不存在 ({self.readme_path})，跳过更新。")
        except Exception as e:
            print(f"更新 README 失败: {e}")


class Notification:
    """消息推送通知管理"""

    @staticmethod
    def send(title: str, content: str = "", image: Optional[str] = None, dry_run: bool = False):
        if dry_run:
            print(f"[DryRun] 跳过发送消息通知: {title}")
            return

        push_types = os.environ.get('PUSH_TYPE', 'bark').lower()
        if 'bark' in push_types:
            Notification._push_bark(title, content, image)

    @staticmethod
    @retry(times=3, delay=2.0)
    def _push_bark(title: str, content: str, image: Optional[str] = None):
        bark_url = os.environ.get('BARK_URL')
        bark_key = os.environ.get('BARK_KEY')

        if not bark_url or not bark_key:
            return

        bark_url = bark_url.rstrip('/')
        post_url = f"{bark_url}/push"

        payload = {
            "device_key": bark_key,
            "title": title,
            "group": "Bing Wallpapers"
        }
        if content:
            payload["markdown"] = content
        if image:
            payload["image"] = image
        else:
            env_img = os.environ.get('BARK_IMAGE')
            if env_img:
                payload["image"] = env_img

        headers = {'Content-Type': 'application/json; charset=utf-8'}
        session = HttpClient.get_session()
        res = session.post(post_url, json=payload, headers=headers, timeout=10)
        res.raise_for_status()
        print(f"[{title}] Bark 消息推送成功。")


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="获取 Bing 每日 UHD 壁纸并写入 EXIF 元数据")
    parser.add_argument("--output-dir", type=str, default=os.environ.get('OUTPUT_DIR', '.'),
                        help="壁纸输出根目录（默认: 环境变量 OUTPUT_DIR 或当前目录）")
    parser.add_argument("--market", type=str, default="zh-CN",
                        help="Bing 壁纸地区代码（默认: zh-CN，例如 en-US, ja-JP）")
    parser.add_argument("--idx", type=int, default=0,
                        help="壁纸日期偏移量（0 为今日，1 为昨日，最多 7 天）")
    parser.add_argument("--dry-run", action="store_true",
                        help="模拟运行，仅抓取信息并检查是否存在，不执行下载、EXIF 写入或通知")
    parser.add_argument("--skip-exif", action="store_true",
                        help="跳过生成带 EXIF 元数据的图片")
    parser.add_argument("--skip-notify", action="store_true",
                        help="跳过发送 Bark 等消息通知")
    return parser.parse_args()


def main():
    args = parse_args()
    print(f"=== Bing Wallpaper Fetcher 启动 [地区: {args.market}, 偏移: {args.idx}] ===")

    result = BingAPI.fetch_wallpaper(market=args.market, idx=args.idx)
    if not result or not result[0] or not result[1]:
        print("未获取到有效的壁纸信息，退出。")
        return

    today, pic_url, title, comment, copy_right = result
    print(f"获取成功：[{today}] {title}")
    if comment:
        print(f"故事背景：{comment}")
    if copy_right:
        print(f"版权信息：{copy_right}")
    print(f"UHD 链接：{pic_url}")

    if args.dry_run:
        print("[DryRun] 模拟运行完成，退出。")
        return

    github_mgr = GitHubManager()
    storage = StorageManager(github_mgr, base_dir=args.output_dir)
    saved_file_path = storage.download_image(today, pic_url, title)

    if saved_file_path:
        if not args.skip_exif:
            ExifManager(storage).attach_exif(saved_file_path, today, title, comment, copy_right)

        ReadmeGenerator(storage).update()

        if not args.skip_notify:
            push_content = f"### {title}\n"
            if comment:
                push_content += f"\n**故事背景**：{comment}\n"
            if copy_right:
                push_content += f"\n**版权信息**：{copy_right}\n"
            push_content += f"\n[点击下载 UHD 原图]({pic_url})"

            Notification.send(title, push_content, pic_url, dry_run=args.dry_run)
    else:
        print(f"[{today}] 壁纸已是最新，无需重复处理。")


if __name__ == '__main__':
    main()
