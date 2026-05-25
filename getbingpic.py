import os
import re
import time
import functools

import requests
import piexif
from PIL import Image


def retry(times=3, delay=2):
    """通用重试装饰器"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(times):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"{func.__name__} 第 {i + 1} 次失败: {e}")
                    if i < times - 1:
                        time.sleep(delay)
            print(f"{func.__name__} 多次失败，已放弃。")
            return None
        return wrapper
    return decorator


class BingAPI:
    """Bing 每日壁纸接口"""
    BASE_URL = 'https://cn.bing.com'
    TIMEOUT = 10
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    @staticmethod
    @retry(times=3, delay=2)
    def fetch_today_wallpaper():
        """获取今日壁纸信息，返回 (日期, 图片URL, 标题, 描述, 版权) 或全 None"""
        url = f'{BingAPI.BASE_URL}/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN'
        try:
            res = requests.get(url, headers=BingAPI.HEADERS, timeout=BingAPI.TIMEOUT)
            res.raise_for_status()
            res.encoding = 'utf8'
            data = res.json().get('images', [])

            if not data:
                print("Bing 接口未返回图像信息。")
                return None, None, None, None, None

            data = data[0]
            pic_url = BingAPI.BASE_URL + data['urlbase'] + '_UHD.jpg'
            today = data['enddate']
            raw_title = data.get('title', '')

            # 从 copyright 字段中拆分出描述和版权信息
            copyright_text = data.get('copyright', '')
            copyright_list = copyright_text.replace('(', '').replace(')', '').split('©')
            comment = copyright_list[0].strip() if len(copyright_list) > 0 else ""
            copy_right = copyright_list[1].strip() if len(copyright_list) > 1 else copyright_text

            clean_title = BingAPI._clean_filename(raw_title)
            return today, pic_url, clean_title, comment, copy_right

        except Exception as e:
            print(f"获取 Bing 壁纸信息失败: {e}")
            raise e

    @staticmethod
    def _clean_filename(words):
        """移除特殊符号，保留中英文、数字，空格替换为下划线"""
        cleaned = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s-]', '', words)
        return re.sub(r'\s+', '_', cleaned).strip('_')


class GitHubManager:
    """GitHub API 交互与缓存管理"""

    def __init__(self):
        self.token = os.environ.get('GITHUB_TOKEN') or os.environ.get('TARGET_REPO_TOKEN')
        self.repo = os.environ.get('WALLPAPER_REPO')
        self._remote_files = None

    def get_remote_files(self):
        """获取远端图片文件列表，支持缓存与认证，规避 Rate Limit"""
        if self._remote_files is not None:
            return self._remote_files

        self._remote_files = []
        if not self.repo:
            return self._remote_files

        api_url = f"https://api.github.com/repos/{self.repo}/git/trees/main?recursive=1"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        if self.token:
            headers['Authorization'] = f"token {self.token}"

        print("正在从 GitHub API 拉取远端文件树...")
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
                print(f"获取远端图片列表失败，状态码: {status_code}")
        except Exception as e:
            print(f"请求 GitHub API 出现异常: {e}")

        return self._remote_files

    @retry(times=3, delay=2)
    def _fetch_tree_with_retry(self, url, headers):
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        return res


class StorageManager:
    """图片存储管理（下载、路径计算、远端查重）"""

    DOWNLOAD_TIMEOUT = 15

    def __init__(self, github_mgr: GitHubManager):
        self.github_mgr = github_mgr
        self.base_dir = os.environ.get('OUTPUT_DIR', '.')
        self.basics_dir = os.path.join(self.base_dir, 'Basics')
        self.exif_dir = os.path.join(self.base_dir, 'Add Exif')

    def download_image(self, date_str, pic_url, title):
        """下载原图到 Basics 目录，已存在返回 None，成功返回文件路径"""
        year, month = date_str[:4], date_str[4:6]
        filename = f"{date_str}_{title}.jpg"

        # 先查重，避免重复下载和推送
        if self._is_image_exist(filename, year, month):
            print(f"[{filename}] 已存在，跳过下载。")
            return None

        target_dir = os.path.join(self.basics_dir, year, month)
        os.makedirs(target_dir, exist_ok=True)
        file_path = os.path.join(target_dir, filename)

        print(f"开始下载：{filename}")
        try:
            content = self._download_request(pic_url)
            if not content:
                raise ValueError("下载内容为空")
        except Exception as e:
            print(f"下载失败: {e}")
            return None

        with open(file_path, 'wb') as f:
            f.write(content)

        print(f"[{filename}] 下载成功！")
        return file_path

    @retry(times=3, delay=2)
    def _download_request(self, pic_url):
        res = requests.get(pic_url, timeout=self.DOWNLOAD_TIMEOUT)
        res.raise_for_status()
        return res.content

    def _is_image_exist(self, filename, year, month):
        """查重：优先检查本地路径，其次比对缓存的远端文件列表"""
        # 本地检查（适用于开发调试环境）
        local_path = os.path.join(self.basics_dir, year, month, filename)
        if os.path.exists(local_path):
            return True

        # 远端检查（比对缓存的列表，无需发起 HEAD 网络请求）
        rel_path = f"Basics/{year}/{month}/{filename}"
        remote_files = self.github_mgr.get_remote_files()
        if rel_path in remote_files:
            return True

        return False

    def get_exif_path(self, date_str, title):
        """计算 EXIF 版本图片的存储路径"""
        year, month = date_str[:4], date_str[4:6]
        target_dir = os.path.join(self.exif_dir, year, month)
        os.makedirs(target_dir, exist_ok=True)
        return os.path.join(target_dir, f"{date_str}_{title}.jpg")


class ExifManager:
    """EXIF 元数据注入"""

    def __init__(self, storage: StorageManager):
        self.storage = storage

    def attach_exif(self, original_path, date_str, title, comment, copyright_text):
        """向原图注入 EXIF 信息并另存"""
        save_path = self.storage.get_exif_path(date_str, title)

        if os.path.exists(save_path):
            print(f"[{os.path.basename(save_path)}] EXIF 版本已存在，跳过。")
            return save_path

        print("正在写入 EXIF 数据...")
        with Image.open(original_path) as img:
            # 部分 Bing 原图不含 EXIF，需初始化空字典
            raw_exif = img.info.get('exif')
            if raw_exif:
                exif_dict = piexif.load(raw_exif)
            else:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "Interop": {}, "1st": {}}

            exif_dict['0th'][piexif.ImageIFD.ImageDescription] = title.encode('utf-8')
            exif_dict['0th'][piexif.ImageIFD.Copyright] = copyright_text.encode('utf-8')
            exif_dict['0th'][piexif.ImageIFD.XPComment] = comment.encode('utf-16le')

            exif_bytes = piexif.dump(exif_dict)
            img.save(save_path, "jpeg", exif=exif_bytes)

        print("EXIF 写入完成。")
        return save_path


class ReadmeGenerator:
    """README 动态图片橱窗生成器"""

    def __init__(self, storage: StorageManager):
        self.storage = storage
        self.readme_path = os.path.join(storage.base_dir, 'README.md')
        self.start_mark = "<!-- gallery_start -->"
        self.end_mark = "<!-- gallery_end -->"

    def _get_remote_pics(self):
        """从 GitHub 管理器缓存列表中获取远端图片路径列表"""
        return self.storage.github_mgr.get_remote_files()

    def _get_local_pics(self):
        """扫描本地 Basics 目录获取壁纸路径列表"""
        pics = []
        if not os.path.exists(self.storage.basics_dir):
            return pics
        for root, _, files in os.walk(self.storage.basics_dir):
            for file in files:
                if file.endswith('.jpg'):
                    rel_path = os.path.relpath(os.path.join(root, file), self.storage.base_dir)
                    pics.append(rel_path.replace('\\', '/'))
        return pics

    def _get_all_pics(self):
        """返回所有壁纸路径列表（逆序、去重）"""
        all_pics = self._get_remote_pics() + self._get_local_pics()
        if not all_pics:
            return []
        return sorted(set(all_pics), reverse=True)

    def _get_latest_3_pics_md(self):
        """获取最新 3 张图片路径并生成 Markdown 片段"""
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

    def get_latest_pic_url(self):
        """返回最新图片的地址，优先使用仓库 raw 链接，否则返回相对路径"""
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
                    print("README.md 更新完成。")
                else:
                    print("README.md 未找到 gallery 标记，跳过更新。")
            else:
                print("README.md 不存在，跳过更新。")
        except Exception as e:
            print(f"更新 README 失败: {e}")


class Notification:
    """消息推送（通过环境变量 PUSH_TYPE 控制启用的推送渠道）"""

    @staticmethod
    def send(title, content="", image=None):
        push_types = os.environ.get('PUSH_TYPE', 'bark').lower()
        if 'bark' in push_types:
            Notification._push_bark(title, content, image)

    @staticmethod
    @retry(times=3, delay=2)
    def _push_bark(title, content, image=None):
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
        res = requests.post(post_url, json=payload, headers=headers, timeout=10)
        res.raise_for_status()
        print(f"[{title}] Bark 推送成功。")


if __name__ == '__main__':
    result = BingAPI.fetch_today_wallpaper()

    if result and result[0] and result[1]:
        today, pic_url, title, comment, copy_right = result
        github_mgr = GitHubManager()
        storage = StorageManager(github_mgr)
        saved_file_path = storage.download_image(today, pic_url, title)

        if saved_file_path:
            ExifManager(storage).attach_exif(saved_file_path, today, title, comment, copy_right)
            ReadmeGenerator(storage).update()
            
            # 丰富推送正文 Markdown 格式
            push_content = f"### {title}\n"
            if comment:
                push_content += f"\n**故事背景**：{comment}\n"
            if copy_right:
                push_content += f"\n**版权信息**：{copy_right}\n"
            push_content += f"\n[点击下载 UHD 原图]({pic_url})"

            Notification.send(title, push_content, pic_url)
        else:
            print(f"[{today}] 壁纸已是最新，无需更新。")
    else:
        print("未获取到壁纸信息，结束执行。")
