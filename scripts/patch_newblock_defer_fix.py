#!/usr/bin/env python3
# 修复 box64 newBlock 单槽 defer 覆盖丢失：
# map128/map64/internal LIST newBlock 在 mutex_blocks 外直接写 defered_prot_* 单槽
# （不持 mutex_prot），与持 mutex_prot 线程的 UNLOCK_PROT flush 并发竞态；
# 多线程并发创建块时后者覆盖前者，被覆盖块的 memprot/mapallmem 簿记永久丢失
# （表现为 >4G 池块 rb_set 后 rb_get_end 查无条目、badpage prot=0、崩溃 B）。
# 修复：三处 newBlock 去掉 if(setting_prot) defer 分支，无条件 setProtection
# （内部 LOCK_PROT 短临界区，创建块不频繁，阻塞可接受）。
# 哨兵：BOX64-BUILD: newblock-defer-fix（幂等）
import sys
from pathlib import Path

SENTINEL = "BOX64-BUILD: newblock-defer-fix"

JOBS = [
    (
        "src/custommem.c",
        [
            # map128_newBlock
            (
                """    last_block_index_map128 = i;
    if(mapallmem) {
        if(setting_prot) {
            // defer the setProtection...
            defered_prot_p = (uintptr_t)p;
            defered_prot_sz = allocsize;
            defered_prot_prot = PROT_READ|PROT_WRITE;
            defered_prot_flags = MEM_ALLOCATED;
        } else
            setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
    }""",
                """    last_block_index_map128 = i;
    if(mapallmem) {
        // BOX64-BUILD: newblock-defer-fix 直接 setProtection（原 defer 单槽并发覆盖丢簿记）
        setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
    }""",
            ),
            # map64_newBlock
            (
                """    if (mapallmem) {
        if (setting_prot) {
            defered_prot_p    = (uintptr_t)p;
            defered_prot_sz   = allocsize;
            defered_prot_prot = PROT_READ | PROT_WRITE;
            defered_prot_flags = MEM_ALLOCATED;
        } else {
            setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
        }""",
                """    if (mapallmem) {
        // BOX64-BUILD: newblock-defer-fix 直接 setProtection（原 defer 单槽并发覆盖丢簿记）
        setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);""",
            ),
            # internal LIST newBlock
            (
                """    last_block_index_list = i;
    if(mapallmem) {
        if(setting_prot) {
            // defer the setProtection...
            defered_prot_p = (uintptr_t)p;
            defered_prot_sz = allocsize;
            defered_prot_prot = PROT_READ|PROT_WRITE;
            defered_prot_flags = MEM_ALLOCATED;
        } else
            setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
    }""",
                """    last_block_index_list = i;
    if(mapallmem) {
        // BOX64-BUILD: newblock-defer-fix 直接 setProtection（原 defer 单槽并发覆盖丢簿记）
        setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
    }""",
            ),
        ],
    ),
]


def apply_file(path: Path, edits) -> bool:
    text = path.read_text(encoding="utf-8")
    if SENTINEL in text:
        print(f"已幂等: {path}")
        return True
    out = []
    pos = 0
    ok = True
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            print(f"锚点命中 {n} 次（需恰好 1 次）: {path}\n---\n{old[:120]}...")
            ok = False
            break
        idx = text.index(old, pos)
        out.append(text[pos:idx])
        out.append(new)
        pos = idx + len(old)
    if not ok:
        return False
    out.append(text[pos:])
    path.write_text("".join(out), encoding="utf-8")
    print(f"已应用: {path}")
    return True


def main():
    if len(sys.argv) != 2:
        print("用法: patch_newblock_defer_fix.py <box64源码目录>")
        sys.exit(2)
    root = Path(sys.argv[1])
    for rel, edits in JOBS:
        if not apply_file(root / rel, edits):
            sys.exit(1)
    print("newblock-defer-fix 完成")


if __name__ == "__main__":
    main()
