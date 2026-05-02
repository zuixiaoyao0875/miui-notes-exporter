#!/usr/bin/env python3
"""
【模块说明：note_renderer.py】
定位：Markdown 渲染引擎（Renderer）。
职责：负责“加工打磨”。专门处理 Pipeline 的 [Step 3]，接收被提取并格式化的 JSON 节点数据，
      搭配内置的富文本正则语法引擎，将私有的 XML 标签如浏览器般渲染输出为高保真的 Markdown 文件。
"""
import os
import json
import re
import html
from datetime import datetime
from pathlib import Path

class MiNoteToMarkdown:
    def __init__(self, json_dir, output_dir, att_dir=None):
        self.json_dir = Path(json_dir)
        self.output_dir = Path(output_dir)
        self.att_dir = Path(att_dir) if att_dir else None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 预编译正则：匹配 MIUI 的 XML 式富文本标签
        self.re_bold = re.compile(r'<text bold="true">(.*?)</text>', re.DOTALL)
        self.re_generic_tag = re.compile(r'<text[^>]*>(.*?)</text>', re.DOTALL)
        self.re_newline = re.compile(r'<br\s*/?>')

    def format_time(self, ts):
        """将毫秒时间戳转换为标准字符串"""
        if not ts: return "N/A"
        try:
            return datetime.fromtimestamp(int(ts) / 1000).strftime('%Y-%m-%d %H:%M:%S')
        except:
            return str(ts)

    def clean_filename(self, name):
        """清理文件名中的非法字符，确保 Windows/Linux 兼容性"""
        if not name: return "Untitled"
        # 1. 移除 Windows 非法字符 \ / : * ? " < > |
        name = re.sub(r'[\\\/\:\*\?\"\<\>\|]', '_', str(name))
        # 2. 移除控制字符、换行符等不可见字符 (ASCII 0-31)
        name = "".join(c for c in name if c.isprintable() and ord(c) >= 32)
        # 3. 移除表情符号等非 BMP 字符 (处理像 ☺ 这种字符)
        name = re.sub(r'[^\u0000-\uFFFF]', '', name)
        # 4. 去除首尾空格及点
        name = name.strip().strip('.')
        return name[:100] or "Untitled"

    def find_asset(self, file_hash):
        """在附件目录中寻找匹配 Hash 的文件（带后缀）"""
        if not self.att_dir or not self.att_dir.is_dir():
            return None
        
        # 尝试直接匹配或以 Hash 开头的文件
        for f in self.att_dir.iterdir():
            if f.stem == file_hash or f.name == file_hash:
                return f.name
        return None

    def parse_rich_text(self, text, assets_map=None):
        """解析 MIUI 富文本标签为 Markdown"""
        if not text: return ""
        
        # 0. 预处理：解码 HTML 实体（如 &lt;, &gt;, &amp; 等）
        text = html.unescape(str(text))
        
        # 1. 转换超链接 <a>
        text = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.IGNORECASE)
        
        # 2. 转换内联样式（加粗、斜体、下划线、删除线、高亮色）
        text = re.sub(r'<text bold="true">(.*?)</text>', r'**\1**', text, flags=re.DOTALL)
        text = re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=re.DOTALL)
        text = re.sub(r'<i>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)
        text = re.sub(r'<u>(.*?)</u>', r'<u>\1</u>', text, flags=re.DOTALL)
        text = re.sub(r'<delete>(.*?)</delete>', r'~~\1~~', text, flags=re.DOTALL)
        text = re.sub(r'<background[^>]*>(.*?)</background>', r'<mark>\1</mark>', text, flags=re.DOTALL)
        
        # 3. 转换标题
        text = re.sub(r'<size>(.*?)</size>', r'# \1', text, flags=re.DOTALL)
        text = re.sub(r'<mid-size>(.*?)</mid-size>', r'## \1', text, flags=re.DOTALL)
        text = re.sub(r'<h3-size>(.*?)</h3-size>', r'### \1', text, flags=re.DOTALL)
        
        # 4. 转换待办事项 Checkbox
        def replace_checkbox(match):
            tag_str = match.group(0)
            checked = 'checked="true"' in tag_str.lower()
            indent_match = re.search(r'indent="(\d+)"', tag_str)
            indent = int(indent_match.group(1)) if indent_match else 1
            spaces = "  " * (indent - 1)
            box = "[x]" if checked else "[ ]"
            return f"{spaces}- {box} "
        text = re.sub(r'<input type="checkbox"[^>]*/>', replace_checkbox, text, flags=re.IGNORECASE)
        
        # 5. 转换列表 (无序 bullet, 有序 order)
        def replace_bullet(match):
            indent_match = re.search(r'indent="(\d+)"', match.group(0))
            indent = int(indent_match.group(1)) if indent_match else 1
            spaces = "  " * (indent - 1)
            return f"{spaces}- "
        text = re.sub(r'<bullet[^>]*/>', replace_bullet, text, flags=re.IGNORECASE)

        def replace_order(match):
            indent_match = re.search(r'indent="(\d+)"', match.group(0))
            indent = int(indent_match.group(1)) if indent_match else 1
            spaces = "  " * (indent - 1)
            return f"{spaces}1. "
        text = re.sub(r'<order[^>]*/>', replace_order, text, flags=re.IGNORECASE)

        # 6. 分割线
        text = re.sub(r'<hr\s*/?>', '\n---\n', text, flags=re.IGNORECASE)
        
        # 7. 引用块 <quote>...</quote>
        def replace_quote(match):
            content = match.group(1)
            lines = content.split('\n')
            quoted = [f"> {line}" if line.strip() else ">" for line in lines]
            return "\n" + "\n".join(quoted) + "\n"
        text = re.sub(r'<quote>(.*?)</quote>', replace_quote, text, flags=re.DOTALL | re.IGNORECASE)

        # 8. 对齐
        text = re.sub(r'<center>(.*?)</center>', r'<div align="center">\1</div>', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<right>(.*?)</right>', r'<div align="right">\1</div>', text, flags=re.DOTALL | re.IGNORECASE)
        
        # 9. 转换多媒体占位符为真实 markdown 链接
        # 9.1 转换图片占位符 ☺ hash<...>
        def replace_img_placeholder(match):
            hash_val = match.group(1)
            file_ref = assets_map.get(hash_val, hash_val) if assets_map else hash_val
            return f"\n\n![Image](../../assets/{file_ref})\n\n"
        text = re.sub(r'☺\s*([a-fA-F0-9]{40})(?:<[^>]*>)*', replace_img_placeholder, text)
        
        # 9.2 转换音频占位符 <sound fileid="..." />
        def replace_sound_placeholder(match):
            hash_val = match.group(1)
            file_ref = assets_map.get(hash_val, hash_val) if assets_map else hash_val
            html_audio = f'\n\n<audio controls>\n  <source src="../../assets/{file_ref}" type="audio/mpeg">\n  您的浏览器不支持 audio 元素。\n</audio>\n\n'
            return html_audio
        text = re.sub(r'<sound[^>]*fileid="([^"]+)"[^>]*>', replace_sound_placeholder, text, flags=re.IGNORECASE)
        
        # 9.3 转换视频占位符 <video fileid="..." />
        def replace_video_placeholder(match):
            hash_val = match.group(1)
            file_ref = assets_map.get(hash_val, hash_val) if assets_map else hash_val
            html_video = f'\n\n<video controls width="100%">\n  <source src="../../assets/{file_ref}" type="video/mp4">\n  您的浏览器不支持 video 元素。\n</video>\n\n'
            return html_video
        text = re.sub(r'<video[^>]*fileid="([^"]+)"[^>]*>', replace_video_placeholder, text, flags=re.IGNORECASE)
        
        # 10. 处理换行
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        
        # 11. 清理所有残留的 XML 式专有标签，但保留已注入的合法 HTML 标签
        text = re.sub(r'</?(?!(?:u|mark|div|a|audio|video|source)\b)[a-zA-Z0-9-]+[^>]*>', '', text, flags=re.IGNORECASE)
        
        # 12. 保证 Markdown 强制换行（在非空行末尾添加两个空格）
        lines = text.split('\n')
        text = '\n'.join([line + '  ' if line.strip() and not line.endswith('  ') else line for line in lines])
        
        return text

    def convert_note(self, json_path):
        """转换单个 JSON 笔记"""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"[!] 无法读取 {json_path}: {e}")
            return

        payload = data.get("payload", {})
        # 获取备用标题，并清理换行符，严格限制长度
        # 获取备用标题
        raw_title = payload.get("title")
        if not raw_title or not str(raw_title).strip():
            raw_title = payload.get("snippet") or "Untitled"
        
        # 1. 解码 HTML 实体（多次解码以防万一，并确保标签被还原）
        raw_title = html.unescape(html.unescape(str(raw_title)))
        
        # 2. 移除 MIUI 特有的占位符
        raw_title = re.sub(r'☺\s*[a-fA-F0-9]{40}.*?', '', raw_title)
        
        # 3. 移除 <style> 和 <script> 及其内部内容
        raw_title = re.sub(r'(?is)<style.*?>.*?</style>', '', raw_title)
        raw_title = re.sub(r'(?is)<script.*?>.*?</script>', '', raw_title)
        
        # 4. 移除所有剩余 HTML 标签（包括 <!DOCTYPE> 和注释）
        raw_title = re.sub(r'<(?:[^"\'>]|"[^"]*"|\'[^\']*\')*>', '', raw_title)
        
        # 5. 清理空白字符：将所有换行、制表符、多个空格合并为一个空格
        raw_title = re.sub(r'\s+', ' ', raw_title).strip()
        
        # 6. 如果清理后变为空，使用默认值
        if not raw_title:
            raw_title = "Untitled"
            
        title = raw_title[:50].strip() # 压缩为最多 50 个字符
        
        # 兼容多种可能的时间戳字段名
        created_ts = payload.get("creation_date") or payload.get("creation_time") or payload.get("tag_4")
        
        # 提取最新的时间戳作为 updated_ts
        max_ts = 0
        def find_max_ts(node):
            nonlocal max_ts
            if isinstance(node, dict):
                for v in node.values():
                    find_max_ts(v)
            elif isinstance(node, list):
                for item in node:
                    find_max_ts(item)
            elif isinstance(node, (int, float)) and 1000000000000 < node < 3000000000000:
                if node > max_ts:
                    max_ts = int(node)
            elif isinstance(node, str) and node.isdigit() and 1000000000000 < int(node) < 3000000000000:
                if int(node) > max_ts:
                    max_ts = int(node)
        find_max_ts(payload)
        updated_ts = max_ts if max_ts > 0 else None
        
        created = self.format_time(created_ts)
        updated = self.format_time(updated_ts)
        
        # 文件夹信息在顶级或 payload 中
        folder = data.get("folder") or data.get("folder_name") or "common"
        # LUID 在 JSON 顶级
        luid = data.get("luid") or payload.get("luid") or "unknown"

        # 构造 Markdown 内容
        md_lines = []
        md_lines.append("---")
        md_lines.append(f"title: \"{title}\"")
        md_lines.append(f"date: {created}")
        md_lines.append(f"updated: {updated}")
        md_lines.append(f"folder: {folder}")
        md_lines.append(f"luid: {luid}")
        md_lines.append("---")
        md_lines.append("")

        # 解析 data_list
        data_list = payload.get("data_list", [])
        if not isinstance(data_list, list):
            data_list = []

        # 提取附件映射
        assets_map = {}
        for item in data_list:
            mime = item.get("mime_type") or item.get("luid") or ""
            if "image" in mime or "audio" in mime or "video" in mime:
                content_hash = item.get("content", "")
                if content_hash:
                    real_file = self.find_asset(content_hash)
                    assets_map[content_hash] = real_file or content_hash

        # 构建正文
        full_content = ""
        rendered_assets = set() # 记录已渲染的附件，避免重复
        
        for item in data_list:
            mime = item.get("mime_type") or item.get("luid") or ""
            content = str(item.get("content", ""))
            
            if mime == "vnd.android.cursor.item/text_note":
                # 将 assets_map 传给 parse_rich_text 以替换占位符
                parsed_text = self.parse_rich_text(content, assets_map)
                # 记录被替换的 hash
                for h in assets_map.keys():
                    if h in content: rendered_assets.add(h)
                full_content += parsed_text
            elif "image" in mime:
                if content not in rendered_assets:
                    file_ref = assets_map.get(content, content)
                    full_content += f"\n\n![Image](../../assets/{file_ref})\n\n"
                    rendered_assets.add(content)
            elif "audio" in mime:
                if content not in rendered_assets:
                    file_ref = assets_map.get(content, content)
                    html_audio = f'\n\n<audio controls>\n  <source src="../../assets/{file_ref}" type="audio/mpeg">\n  您的浏览器不支持 audio 元素。\n</audio>\n\n'
                    full_content += html_audio
                    rendered_assets.add(content)
            elif "video" in mime:
                if content not in rendered_assets:
                    file_ref = assets_map.get(content, content)
                    html_video = f'\n\n<video controls width="100%">\n  <source src="../../assets/{file_ref}" type="video/mp4">\n  您的浏览器不支持 video 元素。\n</video>\n\n'
                    full_content += html_video
                    rendered_assets.add(content)

        # 处理多余的单独 ☺ (如果还有遗漏)
        full_content = full_content.replace('☺', '').strip()
        md_lines.append(full_content)


        # 确定输出路径
        folder_clean = self.clean_filename(folder)
        dest_dir = self.output_dir / folder_clean
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # 文件名：LUID前缀_标题.md (方便排序)
        safe_title = self.clean_filename(title)
        luid_str = str(luid)
        
        # 确保 luid_str 存在且非未知
        if luid_str and luid_str != "unknown":
            # 如果 luid 是纯数字，补齐 3 位以便排序
            if luid_str.isdigit():
                prefix = f"{int(luid_str):03d}"
            else:
                prefix = luid_str[:8]
            filename = f"{prefix}_{safe_title}.md"
        else:
            filename = f"{safe_title}.md"
            
        output_path = dest_dir / filename

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_lines))
        
        return True

    def run(self):
        print(f"[*] 正在将 JSON 转换为 Markdown...")
        print(f"[*] 输入目录: {self.json_dir}")
        print(f"[*] 附件目录: {self.att_dir or '未指定'}")
        print(f"[*] 输出目录: {self.output_dir}")

        json_files = list(self.json_dir.rglob("*.json"))
        # 排除 cache 文件夹中的条目
        json_files = [f for f in json_files if "cache" not in str(f)]
        
        success_count = 0
        for jf in json_files:
            if self.convert_note(jf):
                success_count += 1
        
        print(f"\n[√] 转换完成！")
        print(f"[√] 成功生成: {success_count} 篇 Markdown 笔记")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MIUI Note JSON to Markdown Converter")
    parser.add_argument("--json", required=True, help="Path to JSON notes directory")
    parser.add_argument("--md", required=True, help="Path to output Markdown directory")
    parser.add_argument("--assets", help="Path to attachments directory (miui_att)")
    
    args = parser.parse_args()
    
    converter = MiNoteToMarkdown(args.json, args.md, args.assets)
    converter.run()
