"""
【模块说明：note_parser.py】
定位：Protobuf 结构解析器。
职责：负责“冶炼分类”。专门处理 Pipeline 的 [Step 2]，深入扫描被提取出的 .bak 原始文件，
      把不可读的 Protobuf 二进制字节流转化为结构化的 JSON 树，提取出各个逻辑属性和元数据。
"""
import os
import json
import zipfile
import hashlib
import re
import datetime
import sys
import io
from pathlib import Path

# 移除 sys.stdout 重定向，防止部分环境下缓冲异常导致“无输出”


# ===========================================================================
# MIUI Note Protobuf-to-JSON Aligned Exporter (v31.0 - CODE-AUDIT ALIGNED)
# ===========================================================================
# 逻辑来源：审计 miui-notes-exporter.py (v1.0)
# 核心发现：
# 1. Folder 定义起始于 0x0A 标记位。
# 2. 笔记分类名称存储在 Tag 10 (WireType 2, Marker 0x52)。

class ProtoParser:
    def __init__(self, data, pos=0, end=None):
        self.data = data
        self.pos = pos
        self.end = end if end is not None else len(data)

    def read_varint(self):
        result, shift = 0, 0
        while self.pos < self.end:
            b = self.data[self.pos]; self.pos += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80): break
            shift += 7
        return result

    def is_end(self): return self.pos >= self.end

    def parse(self):
        res = {}
        while not self.is_end():
            try:
                tag_wire = self.read_varint()
                tag, wire = tag_wire >> 3, tag_wire & 0x7
                if tag > 500: break # 合理的 Tag 范围限制
                val = None
                if wire == 0: val = self.read_varint()
                elif wire == 1:
                    val = self.data[self.pos:self.pos+8].hex().upper(); self.pos += 8
                elif wire == 5:
                    val = self.data[self.pos:self.pos+4].hex().upper(); self.pos += 4
                elif wire == 2:
                    l = self.read_varint()
                    raw_val = self.data[self.pos : self.pos + l]
                    self.pos += l
                    try:
                        decoded = raw_val.decode('utf-8')
                        if all(c.isprintable() or c in '\n\r\t' for c in decoded): val = decoded
                        else: val = raw_val
                    except: val = raw_val
                else:
                    if wire == 4: break
                    else: break
                
                if val is not None:
                    if tag in res:
                        if not isinstance(res[tag], list): res[tag] = [res[tag]]
                        res[tag].append(val)
                    else: res[tag] = val
            except: break
        return res

def deep_semantic_decode(payload_dict):
    if not isinstance(payload_dict, dict): return payload_dict
    tag_map = {"1":"luid","2":"meta","4":"content","5":"creation_time","8":"snippet","9":"data_list","10":"folder_id","14":"title"}
    new_res = {}
    for k, v in payload_dict.items():
        label = tag_map.get(str(k), f"tag_{k}")
        if str(k) == "9":
            items = v if isinstance(v, list) else [v]
            new_res[label] = [deep_semantic_decode(brutal_hex_fix(x)) for x in items]
        elif isinstance(v, dict): new_res[label] = deep_semantic_decode(v)
        else: new_res[label] = v
    return new_res

def brutal_hex_fix(val):
    # 处理 bytes：如果是合法的 Proto 消息片段则解析
    if isinstance(val, bytes):
        if len(val) > 2 and (b'vnd.android' in val or b'image/' in val or b'text' in val or b'common' in val):
            parsed = ProtoParser(val).parse()
            if parsed: return parsed
        return val.hex().upper() if not all(32 <= b <= 126 for b in val) else val.decode('utf-8', 'ignore')
    
    # 处理 hex 字符串
    if isinstance(val, str) and len(val) > 40 and all(c in '0123456789ABCDEF' for c in val):
        try:
            b = bytes.fromhex(val)
            if b'vnd.android' in b or b'image/' in b:
                parsed = ProtoParser(b).parse()
                if parsed: return parsed
        except: pass
    return val

def make_json_serializable(obj):
    if isinstance(obj, dict): return {str(k): make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list): return [make_json_serializable(v) for v in obj]
    if isinstance(obj, bytes):
        try: return obj.decode('utf-8')
        except: return obj.hex().upper()
    return obj

class MiNoteAuditReplicator:
    def __init__(self, bak_path, output_dir):
        self.bak_path = Path(bak_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.note_groups = {} # ID -> Title

    def run(self):
        print(f"[*] Starting Audit-Aligned Replicator (v31.0)...")
        if not self.bak_path.exists():
            print(f"[!] 找不到备份文件: {self.bak_path}")
            return
            
        bak_bytes = self.bak_path.read_bytes()
        total_len = len(bak_bytes)
        
        # 1. 预扫描 0x0A (Groups/Folders)
        print("[*] Phase 1: Catching Folder Definitions (0x0A)...", flush=True)
        pos = 0
        while True:
            pos = bak_bytes.find(b'\x0A', pos)
            if pos == -1 or pos >= total_len - 50: break
            
            # 极速预检：0x0A 后必须跟一个合理的长度和起始标记 (0x08 或 0x12)
            try:
                p_tmp = pos + 1
                l_g, shift = 0, 0
                while p_tmp < pos + 5 and p_tmp < total_len:
                    b = bak_bytes[p_tmp]; p_tmp += 1
                    l_g |= (b & 0x7F) << shift
                    if not (b & 0x80): break
                    shift += 7
                
                if 0 < l_g < 20000 and p_tmp < total_len and bak_bytes[p_tmp] in [0x08, 0x12]:
                    data = ProtoParser(bak_bytes, pos=p_tmp, end=p_tmp + l_g).parse()
                    if 2 in data and 9 in data:
                        gid = data.get(2)
                        gtitle = data.get(9)
                        if isinstance(gid, list): gid = next((x for x in gid if x), "unknown")
                        if isinstance(gtitle, list): gtitle = next((x for x in gtitle if x), "common")
                        
                        self.note_groups[str(gid)] = str(gtitle)
                        print(f"[*] Identified Folder: {gtitle} (ID: {gid})", flush=True)
                        pos = p_tmp + l_g
                        continue
            except: pass
            pos += 1
        print(f"[*] Phase 1 complete. Folders found: {len(self.note_groups)}", flush=True)

        # 2. 正文打捞并对齐 Tag 10 (Folder Name)
        print("[*] Phase 2: Salvaging Notes & Aligning Tag 10...")
        pos = 0
        # 2. 正文深度打捞 (Deep Scan for Notes)
        print("[*] Phase 2: Salvaging Notes (Deep Scan)...", flush=True)
        pos = 0
        final_notes_map = {} # luid -> note_entry
        for marker in [b'\x12', b'\x1A']:
            print(f"[*] Scanning marker {marker.hex()}...", flush=True)
            last_print = 0
            while True:
                pos = bak_bytes.find(marker, pos)
                if pos == -1 or pos >= total_len - 50: break
                
                if pos - last_print > 2 * 1024 * 1024:
                    print(f"[*] Marker {marker.hex()} Progress: {pos/1024/1024:.1f}MB / {total_len/1024/1024:.1f}MB", flush=True)
                    last_print = pos
                
                p_e = ProtoParser(bak_bytes, pos=pos+1)
                try:
                    l_e = p_e.read_varint()
                    if 10 < l_e < 10 * 1024 * 1024:
                        first_tag_pos = p_e.pos
                        raw_res = ProtoParser(bak_bytes, pos=first_tag_pos, end=first_tag_pos + l_e).parse()
                        
                        # 评分制判定：包含越多特征标签，越可能是笔记
                        note_tags = [1, 2, 4, 8, 10, 14]
                        score = sum(1 for t in note_tags if t in raw_res)
                        
                        if score >= 4:
                            # 提取标识符（优先 Tag 1，备选 Tag 2）
                            luid = raw_res.get(1) or raw_res.get(2)
                            if isinstance(luid, list): luid = luid[0]
                            if not luid: luid = f"off_{pos}"
                            luid_str = str(luid)
                            
                            # 文件夹对齐
                            folder_id = raw_res.get(10)
                            if isinstance(folder_id, list): folder_id = folder_id[0]
                            folder_name = self.note_groups.get(str(folder_id))
                            if not folder_name:
                                folder_name = folder_id if isinstance(folder_id, str) else "common"

                            note_entry = {
                                "payload": deep_semantic_decode(raw_res),
                                "folder": str(folder_name),
                                "offset": pos,
                                "_score": score
                            }
                            
                            # 权重去重：保留特征得分最高的条目
                            if luid_str not in final_notes_map or score > final_notes_map[luid_str].get("_score", 0):
                                final_notes_map[luid_str] = note_entry
                            
                            pos = first_tag_pos + l_e
                            continue
                except: pass
                pos += 1
        
        final_notes = list(final_notes_map.values())

        # 3. 通用筛选与导出 (Generic Filtering & Export)
        exported_count = 0
        for i, n in enumerate(final_notes):
            p = n['payload']
            
            # 综合筛选逻辑：确保找回所有真实笔记，同时剔除系统碎片
            # 1. 核心特征：具备有效的 data_list 正文结构 (MIUI 笔记标准存储)
            has_valid_dl = False
            dl = p.get("data_list")
            if dl and isinstance(dl, list):
                for item in dl:
                    if isinstance(item, dict) and item.get("content"):
                        has_valid_dl = True; break
            
            # 2. 辅助特征：具备实质意义的标题 (排除 16 位及以上的纯 Hex 哈希碎片)
            has_meaningful_title = False
            title = p.get("title")
            if isinstance(title, str) and len(title.strip()) > 0:
                # 真实标题通常包含中文、空格或非 Hex 字符；若为 16 位以上 Hex 则视为碎片标识
                if not re.fullmatch(r'[0-9A-Fa-f]{16,}', title.strip()):
                    has_meaningful_title = True
            
            # 只要满足正文有效或标题有意义，即视为我们要的“真实笔记”
            if not (has_valid_dl or has_meaningful_title):
                # 存入 cache 文件夹以供保险备查 (Save fragments to cache for audit)
                cache_dir = os.path.join(self.output_dir, "cache")
                if not os.path.exists(cache_dir): os.makedirs(cache_dir)
                fn_cache = f"filtered_{i:03d}_at_{n['offset']}.json"
                with open(os.path.join(cache_dir, fn_cache), 'w', encoding='utf-8') as f:
                    json.dump(make_json_serializable(n), f, indent=4, ensure_ascii=False)
                continue

            exported_count += 1
            # 截断文件夹名称（最多30个字符）并清理非法字符
            raw_folder = n["folder"]
            clean_folder = re.sub(r'[\\\/\:\*\?\"\<\>\|]', '', str(raw_folder))[:30].strip() or "common"
            cat_dir = os.path.join(self.output_dir, clean_folder)
            if not os.path.exists(cat_dir): os.makedirs(cat_dir)
            
            title = p.get("title", f"Note_{exported_count}")
            if not isinstance(title, str) or not title.strip():
                title = f"Note_{exported_count}"
            
            clean_t = re.sub(r'[\\\/\:\*\?\"\<\>\|]', '', str(title))[:35].strip() or "Note"
            fn = f"{exported_count:03d}_{clean_t}.json"
            
            res_json = make_json_serializable({
                "luid": p.get("luid", exported_count),
                "folder_name": n["folder"],
                "content_decoded": p.get("content", ""),
                "payload": p,
                "_meta": {"offset": n["offset"]}
            })
            with open(os.path.join(cat_dir, fn), 'w', encoding='utf-8') as f:
                json.dump(res_json, f, indent=4, ensure_ascii=False)

        print(f"\n[!] AUDIT-ALIGNED SUCCESS! Total exported: {exported_count} (Filtered from {len(final_notes)})")
        print(f"[*] Final Assets: {self.output_dir}")

if __name__ == "__main__":
    # 示例运行逻辑
    BAK_P = r"D:\Users\ZXY\Desktop\pyvenv\qny\example.bak"
    OUT_R = r"D:\Users\ZXY\Desktop\pyvenv\qny\output"
    MiNoteAuditReplicator(BAK_P, OUT_R).run()
