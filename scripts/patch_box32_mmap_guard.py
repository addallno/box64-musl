#!/usr/bin/env python3
"""box32 早期启动崩溃修复：box32_dynarec_mmap 在 mapallmem 未初始化（NULL）时
fallback 到 InternalMmap，修 32-bit ELF 启动 SIGSEGV。

根因链（bin 3911c89c/18e90ff5 打点定位）：
  NewBox64Context → init_custommem_helper → blockstree=rbtree_init
  → customMalloc → 新建块三目 (box64_is32bits ? box32_dynarec_mmap : InternalMmap)
  → box32_dynarec_mmap → rb_get_end(mapallmem=NULL) → tree->root 解引用 NULL → SIGSEGV
  （64 位走 InternalMmap 不经过；32 位进程全局 box64_is32bits=1 必踩）
  mapallmem 在 init_custommem_helper 后段才创建，任何先于它的 customMalloc 均触发。
"""
import os
import sys

SENTINEL = "BOX64-BUILD: box32-mmap-guard"

ANCHOR = (
    "void* box32_dynarec_mmap(size_t size, int fd, off_t offset)\n"
    "{\n"
    "    #ifdef BOX32\n"
)
INSERT = (
    "    if(!mapallmem) {  // " + SENTINEL + "\n"
    "        // mapallmem 未建（init 早期的 customMalloc）：不能走 rb_get_end(NULL)，\n"
    "        // 直接普通 mmap（与函数尾 fallback 语义一致，保留 4GB+ 选址偏好）\n"
    "        uint32_t mf = MAP_PRIVATE | ((fd==-1)?MAP_ANONYMOUS:0);\n"
    "        return InternalMmap(box64_isAddressSpace32?NULL:(void*)0x100000000ULL,\n"
    "            size, PROT_READ | PROT_WRITE | PROT_EXEC, mf, fd, offset);\n"
    "    }\n"
)


def patch(root: str) -> int:
    p = os.path.join(root, "src", "custommem.c")
    with open(p, "r", encoding="utf-8") as f:
        src = f.read()
    if SENTINEL in src:
        print("patch_box32_mmap_guard: 已应用过，跳过")
        return 0
    if ANCHOR not in src:
        print("patch_box32_mmap_guard: 锚未命中", file=sys.stderr)
        return 1
    src = src.replace(ANCHOR, ANCHOR + INSERT, 1)
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    print("patch_box32_mmap_guard: 完成")
    return 0


if __name__ == "__main__":
    sys.exit(patch(sys.argv[1] if len(sys.argv) > 1 else "."))
