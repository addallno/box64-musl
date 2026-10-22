#!/usr/bin/env python3
# badpage 第 4 清除通道探针（probe21）：
# setp64chk 已证 0x10718x 族写入成功（锁内 hasend=1）后被清，memun 三通道全 0。
# 本探针下沉到 rbtree 层：memprot 树 + 相交 [0x107000000,0x108000000) 的
# rb_set_64/rb_unset 全量打印 caller（badpage 实际在 0x100000000 族，范围已修正），抓"谁最后写了 prot=0"；
# 另在 refreshProtection 抓 prot&~PROT_CUSTOM==0 的 mprotect PROT_NONE（硬件 ERR=5 上膛时刻）。
# 哨兵：BOX64-BUILD: memprot-wipe-probe（幂等）
import sys
from pathlib import Path

SENTINEL = "BOX64-BUILD: memprot-wipe-probe"

RBTREE_HELPER = """// BOX64-BUILD: memprot-wipe-probe memprot 树小范围写追踪（手写比较避免 string.h 依赖）
static int wipeprobe_memprot(const char* n)
{
    return n && n[0]=='m' && n[1]=='e' && n[2]=='m' && n[3]=='p' && n[4]=='r' && n[5]=='o' && n[6]=='t' && n[7]==0;
}
static int wipeprobe_hit(uintptr_t s, uintptr_t e)
{
    return s < 0x101000000ULL && e > 0x100000000ULL;
}
static int wipeprobe_hit7f(uintptr_t s, uintptr_t e)
{
    return s < 0x8000000000ULL && e > 0x7f00000000ULL;
}
"""

JOBS = [
    (
        "src/tools/rbtree.c",
        [
            (
                """#include <stdbool.h>
""",
                """#include <stdio.h>
#include <stdbool.h>
""",
            ),
            (
                """int rb_set_64(rbtree_t *tree, uintptr_t start, uintptr_t end, uint64_t data) {
""",
                RBTREE_HELPER + """int rb_set_64(rbtree_t *tree, uintptr_t start, uintptr_t end, uint64_t data) {
    // BOX64-BUILD: memprot-wipe-probe rbtree 层 memprot 写入
    if(wipeprobe_memprot(tree->name) && (wipeprobe_hit(start, end) || (wipeprobe_hit7f(start, end) && data == 0)))
        fprintf(stderr, "[memset] caller=%p %lx-%lx data=%x\\n", __builtin_return_address(0), (unsigned long)start, (unsigned long)end, (unsigned)(uint32_t)data);
""",
            ),
            (
                """int rb_unset(rbtree_t *tree, uintptr_t start, uintptr_t end) {
""",
                """int rb_unset(rbtree_t *tree, uintptr_t start, uintptr_t end) {
    // BOX64-BUILD: memprot-wipe-probe rbtree 层 memprot 删除
    if(wipeprobe_memprot(tree->name) && (wipeprobe_hit(start, end) || wipeprobe_hit7f(start, end)))
        fprintf(stderr, "[munset] caller=%p %lx-%lx\\n", __builtin_return_address(0), (unsigned long)start, (unsigned long)end);
""",
            ),
        ],
    ),
    (
        "src/custommem.c",
        [
            (
                """        int ret = mprotect((void*)ALIGN_DOWN(addr), box64_pagesize, prot&~PROT_CUSTOM);
""",
                """        int ret = mprotect((void*)ALIGN_DOWN(addr), box64_pagesize, prot&~PROT_CUSTOM);
        // BOX64-BUILD: memprot-wipe-probe 簿记 prot=0 时 mprotect PROT_NONE（崩溃 B 硬件 ERR=5 上膛）
        if(box64_is32bits && !(prot&~PROT_CUSTOM))
            fprintf(stderr, "[refprot-none] addr=%p prot=%x caller=%p\\n", (void*)ALIGN_DOWN(addr), (unsigned)prot, __builtin_return_address(0));
""",
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
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            print(f"锚点命中 {n} 次（需恰好 1 次）: {path}\n---\n{old[:120]}...")
            return False
        idx = text.index(old, pos)
        out.append(text[pos:idx])
        out.append(new)
        pos = idx + len(old)
    out.append(text[pos:])
    path.write_text("".join(out), encoding="utf-8")
    print(f"已应用: {path}")
    return True


def main():
    if len(sys.argv) != 2:
        print("用法: patch_memprot_wipe_probe.py <box64源码目录>")
        sys.exit(2)
    root = Path(sys.argv[1])
    for rel, edits in JOBS:
        if not apply_file(root / rel, edits):
            sys.exit(1)
    print("memprot-wipe-probe 完成")


if __name__ == "__main__":
    main()
