#!/usr/bin/env python3
"""patch_dlclose_guard.py — cleanDBFromAddressRange 解引用前 db 有效性校验（A型 use-after-free 自用加固）

根因（2026-10-11 轮30-32取证）：
- A型崩溃=FreeRangeDynablock(dynablock.c:318) 解引用野 db。
- badprobe(patch_getdb_probe.py) 只验 db 指针形状(对齐/48位范围)，
  但 db 指向的内存已被释放复用改写（db->x64_addr/db->block 等字段
  成 0x800004f9443018 等非 dynarec 野值）时验不出。
- DelMmaplist(dlclose卸载路径)全程无锁，与并发 munmap/malloc 复用竞态。

加固（cleanDBFromAddressRange 的 if(db) switch 前，覆盖 destroy=0/1/2 全部）：
1) rb_get_64(rbt_dynmem, db) 校验 db 指向有效 dynarec chunk（已 rb_unset 则拦）；
2) db->x64_addr 形状校验：guest 地址应 >=0x400000、<=0xffffffffffff(48位)、
   页对齐(&0xfff==0)；垃圾 0x80.../小枚举/未对齐 → 跳过+取证日志。

不改上游、CI 构建时注入、仅本地自用（box64 上游不接受AI代码）。
哨兵：BOX64-BUILD: dlclose-guard（幂等）
用法：python3 patch_dlclose_guard.py <box64源码目录>
退出码：0=成功/已幂等，1=锚点缺失
"""
import sys
from pathlib import Path

SENTINEL = "BOX64-BUILD: dlclose-guard"

JOBS = [
    (
        "src/custommem.c",
        [
            (
                """        if(db) {
            ret = 1;
            switch(destroy) {
                case 0: MarkRangeDynablock(db, addr, size); break;
                case 1: FreeRangeDynablock(db, addr, size); break;
                case 2: MarkCRCRangeDynablock(db, addr, size); break;
            }

        }
""",
                """        if(db) {
            // BOX64-BUILD: dlclose-guard 解引用前校验 db 有效性（拦 use-after-free 复用内存）
            if(rbt_dynmem && !rb_get_64(rbt_dynmem, (uintptr_t)db)) {
                fprintf(stderr, "[box64] dlclose-guard stale db=%p addr=%p destroy=%d (not in dynmem)\\n",
                    (void*)db, (void*)addr, destroy);
                db = NULL;
                continue;
            }
            if((((uintptr_t)db->x64_addr) < 0x400000) || (((uintptr_t)db->x64_addr) > 0xffffffffffffULL) || (((uintptr_t)db->x64_addr) & 0xfff)) {
                fprintf(stderr, "[box64] dlclose-guard bad x64_addr=%p db=%p x64_size=%zu addr=%p destroy=%d\\n",
                    (void*)db->x64_addr, (void*)db, (size_t)db->x64_size, (void*)addr, destroy);
                db = NULL;
                continue;
            }
            ret = 1;
            switch(destroy) {
                case 0: MarkRangeDynablock(db, addr, size); break;
                case 1: FreeRangeDynablock(db, addr, size); break;
                case 2: MarkCRCRangeDynablock(db, addr, size); break;
            }

        }
""",
            ),
        ],
    ),
]


def main() -> None:
    if len(sys.argv) != 2:
        print("用法: patch_dlclose_guard.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    srcdir = Path(sys.argv[1])
    for i, (rel, variants) in enumerate(JOBS, 1):
        p = srcdir / rel
        if not p.is_file():
            print(f"文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
        s = p.read_text(encoding="utf-8")
        result = None
        for anchor, repl in variants:
            if anchor in s:
                s = s.replace(anchor, repl, 1)
                result = "patched"
                break
            if repl in s:
                result = "skipped"
                break
        if result is None:
            print(f"patch_dlclose_guard: {rel} job{i} 锚点缺失", file=sys.stderr)
            sys.exit(1)
        if result == "patched":
            p.write_text(s, encoding="utf-8")
            print(f"patch_dlclose_guard: {rel} job{i} 已打补丁")
        else:
            print(f"patch_dlclose_guard: {rel} job{i} 已应用过，跳过")
    sys.exit(0)


if __name__ == "__main__":
    main()
