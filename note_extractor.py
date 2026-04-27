#!/usr/bin/env python3
"""
【模块说明：note_extractor.py】
定位：底层物理提取器。
职责：负责“挖矿”。专门负责 Pipeline 的 [Step 1]，即直接物理操作 MIUI 备份的 .bak/.tar 归档文件。
      通过比特流指纹扫描，强制剥离并切出原始媒体资源（如图片、录音等），无视普通的加密或损坏。
"""
from __future__ import annotations

import argparse
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Optional


def detect_image_suffix(path: Path) -> Optional[str]:
    """
    根据文件头判断常见图片格式，返回后缀。
    不依赖 file 命令，纯 Python。
    """
    with path.open("rb") as f:
        head = f.read(32)

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif"
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"BM"):
        return ".bmp"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return ".tiff"

    return None


def find_android_backup_offset(data: bytes) -> int:
    """
    在 MIUI .bak 中查找 ANDROID BACKUP 头起始位置。
    """
    marker = b"ANDROID BACKUP\n"
    pos = data.find(marker)
    if pos == -1:
        raise ValueError("未找到 ANDROID BACKUP 头，文件可能不是 MIUI/Android Backup 格式")
    return pos


def parse_android_backup_header(ab_data: bytes) -> tuple[int, str, str, str]:
    """
    解析 Android Backup 头。
    返回:
      payload_offset, version, compressed_flag, encryption
    头格式通常是:
      ANDROID BACKUP\n
      5\n
      0\n
      none\n
    """
    marker = b"ANDROID BACKUP\n"
    if not ab_data.startswith(marker):
        raise ValueError("输入数据不是以 ANDROID BACKUP 头开头")

    lines = []
    start = 0
    newline_count = 0

    for i, b in enumerate(ab_data):
        if b == 0x0A:  # '\n'
            lines.append(ab_data[start:i].decode("utf-8", errors="replace"))
            start = i + 1
            newline_count += 1
            if newline_count == 4:
                payload_offset = i + 1
                break
    else:
        raise ValueError("ANDROID BACKUP 头不完整")

    if len(lines) < 4:
        raise ValueError("ANDROID BACKUP 头行数不足")

    magic, version, compressed_flag, encryption = lines[:4]
    if magic != "ANDROID BACKUP":
        raise ValueError(f"头标识异常: {magic!r}")

    return payload_offset, version, compressed_flag, encryption


def safe_extract_tar(tar_path: Path, out_dir: Path) -> None:
    """
    安全解压 tar，防止路径穿越。
    """
    out_dir = out_dir.resolve()

    with tarfile.open(tar_path, "r") as tf:
        for member in tf.getmembers():
            member_path = (out_dir / member.name).resolve()
            if not str(member_path).startswith(str(out_dir)):
                raise ValueError(f"tar 成员路径非法: {member.name}")
        tf.extractall(out_dir)


def extract_target_bak(zip_path: Path, pkg: str, out_dir: Path) -> Path:
    """
    从 zip 中提取目标 .bak，重命名为:
      压缩包名.bak
    """
    bak_out = out_dir / f"{zip_path.stem}_com.miui.notes.bak"

    with zipfile.ZipFile(zip_path, "r") as zf:
        matches = [
            name for name in zf.namelist()
            if pkg in name and name.lower().endswith(".bak")
        ]

        if not matches:
            raise FileNotFoundError(f"压缩包中未找到包含 {pkg} 的 .bak 文件")

        target = matches[0]
        print(f"[1/7] 命中 .bak: {target}")

        with zf.open(target) as src, bak_out.open("wb") as dst:
            shutil.copyfileobj(src, dst)

    print(f"[2/7] 已提取: {bak_out}")
    return bak_out


def convert_bak_to_ab(bak_path: Path, ab_path: Path) -> tuple[int, bytes]:
    """
    从 MIUI .bak 中动态定位 ANDROID BACKUP 头，并生成 .ab
    """
    data = bak_path.read_bytes()
    ab_offset = find_android_backup_offset(data)

    with ab_path.open("wb") as f:
        f.write(data[ab_offset:])

    print(f"[3/7] 已生成 .ab: {ab_path}")
    print(f"      ANDROID BACKUP 起始偏移: {ab_offset}")
    return ab_offset, data[ab_offset:ab_offset + 128]


def convert_ab_to_tar(ab_path: Path, tar_path: Path) -> tuple[str, str, str, int]:
    """
    从 .ab 中动态解析头并提取 payload 到 tar。
    仅支持：
      compressed = 0
      encryption = none
    """
    ab_data = ab_path.read_bytes()
    payload_offset, version, compressed_flag, encryption = parse_android_backup_header(ab_data)

    print(f"[4/7] Android Backup 头解析结果:")
    print(f"      version     = {version}")
    print(f"      compressed  = {compressed_flag}")
    print(f"      encryption  = {encryption}")
    print(f"      payload 偏移 = {payload_offset}")

    if compressed_flag != "0":
        raise NotImplementedError("当前脚本暂不处理压缩型 Android Backup（compressed != 0）")

    if encryption.lower() != "none":
        raise NotImplementedError("当前脚本暂不处理加密型 Android Backup（encryption != none）")

    with tar_path.open("wb") as f:
        f.write(ab_data[payload_offset:])

    print(f"[5/7] 已生成 .tar: {tar_path}")
    return version, compressed_flag, encryption, payload_offset


def rename_attachments(att_dir: Path) -> tuple[int, int]:
    """
    自动为 miui_att 下无后缀的图片补后缀。
    """
    if not att_dir.is_dir():
        print(f"[6/7] 未找到附件目录，跳过: {att_dir}")
        return 0, 0

    renamed = 0
    skipped = 0

    for f in att_dir.iterdir():
        if not f.is_file():
            continue

        if f.suffix:
            skipped += 1
            continue

        suffix = detect_image_suffix(f)
        if suffix:
            new_path = f.with_name(f.name + suffix)
            f.rename(new_path)
            renamed += 1
        else:
            skipped += 1

    print(f"[6/7] 附件处理完成: 重命名 {renamed} 个，跳过 {skipped} 个")
    return renamed, skipped


def run(zip_path: Path, base_out_dir: Path, pkg: str) -> None:
    """
    整体流程：
      zip -> bak -> ab -> tar -> 解压 -> 附件补后缀
    输出目录：
      ./tmp/压缩包同名文件夹/
    """
    if not zip_path.is_file():
        raise FileNotFoundError(f"找不到 zip 文件: {zip_path}")

    # 输出目录改为 ./tmp/压缩包同名文件夹
    out_dir = base_out_dir / zip_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    base = zip_path.stem
    bak_path = out_dir / f"{base}_{pkg}.bak"
    ab_path = out_dir / f"{base}.ab"
    tar_path = out_dir / f"{base}.tar"

    extract_target_bak(zip_path, pkg, out_dir)
    convert_bak_to_ab(bak_path, ab_path)
    convert_ab_to_tar(ab_path, tar_path)

    safe_extract_tar(tar_path, out_dir)
    print(f"[7/7] 已解压到: {out_dir}")

    att_dir = out_dir / "apps" / pkg / "miui_att"
    rename_attachments(att_dir)

    print()
    print("完成。")
    print(f"输出目录: {out_dir}")
    print(f"笔记目录: {out_dir / 'apps' / pkg}")
    print(f"正文候选文件: {out_dir / 'apps' / pkg / 'miui_bak' / '_tmp_bak'}")
    print(f"元数据候选文件: {out_dir / 'apps' / pkg / 'miui_meta' / 'cache' / '_tmp_meta'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="提取 MIUI notes 备份：zip -> bak -> ab -> tar -> 解压"
    )
    parser.add_argument(
        "zip_path",
        help="MIUI 备份 zip 路径，例如 /storage/emulated/0/MIUI/backup/AllBackup/20260417_023926.zip",
    )
    parser.add_argument(
        "--out",
        default="./tmp",
        help="基础输出目录，实际会输出到 ./tmp/压缩包同名文件夹",
    )
    parser.add_argument(
        "--pkg",
        default="com.miui.notes",
        help="应用包名，默认 com.miui.notes",
    )

    args = parser.parse_args()

    run(
        zip_path=Path(args.zip_path),
        base_out_dir=Path(args.out),
        pkg=args.pkg,
    )


if __name__ == "__main__":
    main()