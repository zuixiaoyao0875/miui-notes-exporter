#!/usr/bin/env python3
"""
【模块说明：main.py】
定位：整个程序的总调度入口（Entry Point）。
职责：负责“发号施令”。作为完整的四步流水线（Pipeline）架构的总控核心，
      它按顺序串联了物理提取（extractor）、结构解析（parser）和最终渲染（renderer）。
      使用者只需运行此脚本（python main.py）即可一键完成全流程转换。
"""
import os
import shutil
import argparse
from pathlib import Path

# 导入现有模块逻辑
import note_extractor
from note_parser import MiNoteAuditReplicator
from note_renderer import MiNoteToMarkdown

class MiNoteOneKey:
    def __init__(self, zip_path, base_out_dir):
        self.zip_path = Path(zip_path)
        self.zip_stem = self.zip_path.stem
        self.work_dir = Path(base_out_dir) / self.zip_stem
        
        # 定义子目录
        self.raw_dir = self.work_dir / "raw"
        self.assets_dir = self.work_dir / "assets"
        self.json_dir = self.work_dir / "json"
        self.md_dir = self.work_dir / "markdown"

    def setup_dirs(self):
        """创建工作空间结构"""
        for d in [self.raw_dir, self.assets_dir, self.json_dir, self.md_dir]:
            d.mkdir(parents=True, exist_ok=True)
        print(f"[*] 工作空间已就绪: {self.work_dir}")

    def step1_extract(self):
        """步骤 1: 快速解压资源 (使用 extract_miui_notes 逻辑)"""
        print(f"\n[Step 1/3] 正在解压备份资源...")
        # 调用 note_extractor 的 run 函数
        # 注意：run 会在 raw_dir 下创建另一个同名文件夹，我们需要协调一下
        note_extractor.run(self.zip_path, self.raw_dir, "com.miui.notes")
        
        # 寻找解压后的附件目录并移动到 assets
        src_att = self.raw_dir / self.zip_stem / "apps" / "com.miui.notes" / "miui_att"
        if src_att.exists():
            print(f"[*] 正在整理附件到 assets 目录...")
            # 将 miui_att 中的内容移动/复制到 assets
            for f in src_att.iterdir():
                shutil.move(str(f), str(self.assets_dir / f.name))
            print(f"[√] 附件已就绪: {self.assets_dir}")
        else:
            print("[!] 未发现附件目录，跳过资源整理。")

    def step2_parse_json(self):
        """步骤 2: 生成审计级 JSON (使用 buff2json 逻辑)"""
        print(f"\n[Step 2/3] 正在解析笔记数据并生成 JSON...")
        # 定位解压后的 .bak 文件
        # extract_miui_notes 会在 raw_dir/zip_stem 下生成 .bak
        bak_file = self.raw_dir / self.zip_stem / f"{self.zip_stem}_com.miui.notes.bak"
        
        if not bak_file.exists():
            # 尝试搜索 bak
            baks = list(self.raw_dir.rglob("*.bak"))
            if baks: bak_file = baks[0]
        
        if not bak_file.exists():
            print(f"[!] 找不到备份文件 (.bak)，无法生成 JSON。")
            return False

        replicator = MiNoteAuditReplicator(str(bak_file), str(self.json_dir))
        replicator.run()
        print(f"[√] JSON 已生成在: {self.json_dir}")
        return True

    def step3_convert_md(self):
        """步骤 3: 转换为 Markdown (使用 one2md 逻辑)"""
        print(f"\n[Step 3/3] 正在转换为 Markdown...")
        converter = MiNoteToMarkdown(str(self.json_dir), str(self.md_dir), str(self.assets_dir))
        converter.run()
        print(f"[√] Markdown 已输出至: {self.md_dir}")

    def run(self):
        print(f"==========================================")
        print(f"   MIUI Notes One-Key Export Tool v1.0    ")
        print(f"==========================================")
        print(f"[*] 目标备份: {self.zip_path.name}")
        
        self.setup_dirs()
        self.step1_extract()
        if self.step2_parse_json():
            self.step3_convert_md()
        
        print(f"\n[*] 全部任务已完成！")
        print(f"[*] 最终导出目录: {self.work_dir}")
        print(f"==========================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MIUI 笔记全流程一键导出工具 (解压->JSON->Markdown)")
    parser.add_argument("zip", help="MIUI 备份 ZIP 文件路径")
    parser.add_argument("--out", default="./export_result", help="输出根目录")
    
    args = parser.parse_args()
    
    worker = MiNoteOneKey(args.zip, args.out)
    worker.run()
