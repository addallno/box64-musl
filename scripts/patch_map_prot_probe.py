#!/usr/bin/env python3
"""patch_map_prot_probe.py — map128/map64 hint 路径 mincore VMA 探针 + 高区 munmap 时间线

背景（崩溃 B 排查，bin d52e3698 addr2line 定位）：
- SIGSEGV native pc = map128_customMalloc:651 / map64_customMalloc:785（均为 hint 缓存路径读块数据）
- 崩溃地址 0x7f8c…（宿主 mmap 池形态）：#PF ERR=5 页不存在、maps 多时点无该段、
  memExist=17(MEM_EXTERNAL)/prot=0 —— 簿记 EXTERNAL 残留 + VMA 已消失
- 簿记（getProtection/memExist）不可信，用 mincore 真实探 VMA 存在性
- 附带 EXPORT munmap 高区(>4G)时间线打印，定位谁 unmap 了宿主池页

不改上游、CI 构建时注入。哨兵：BOX64-BUILD: map-prot-probe（幂等）
用法：python3 patch_map_prot_probe.py <box64源码目录>
退出码：0=成功/已幂等，1=锚点缺失
"""
import sys
from pathlib import Path

SENTINEL = "BOX64-BUILD: map-prot-probe"

# helper 插在 map128_customMalloc 函数定义前
HELPER_ANCHOR = "void* map128_customMalloc(size_t size, int is32bits)"
HELPER_CODE = """// BOX64-BUILD: map-prot-probe mincore 探 VMA 存在性（0=有映射，-1=无 VMA/越界）
static int probe_vma(const void* p)
{
    unsigned char vec = 0;
    if(!p) return -1;
    return mincore((void*)(((uintptr_t)p) & ~(size_t)(box64_pagesize-1)), box64_pagesize, &vec);
}
"""

JOBS = [
    (
        "src/custommem.c",
        [
            (
                HELPER_ANCHOR,
                HELPER_CODE + HELPER_ANCHOR,
            ),
            (
                """        if(hint && hint->block && (hint->type == BTYPE_MAP) && hint->maxfree && (hint->is32bits==is32bits)) {
""",
                """        if(hint && hint->block && (hint->type == BTYPE_MAP) && hint->maxfree && (hint->is32bits==is32bits)) {
            // BOX64-BUILD: map-prot-probe hint 路径全块逐页 prot+vma 检查（抓中间页 PROT_NONE/消失）
            for(uintptr_t pg=(uintptr_t)hint->block; pg<(uintptr_t)hint->block+hint->size; pg+=0x1000) {
                if(!probe_vma((void*)pg) || !(getProtection(pg)&PROT_READ)) {
                    fprintf(stderr, "[map128probe] badpage=%p prot=%d vma=%d hint=%p block=%p first=%p lowest=%u n=%d last=%d\\n",
                        (void*)pg, getProtection(pg), probe_vma((void*)pg),
                        hint, hint->block, hint->first, hint->lowest, n_blocks, last_block_index_map128);
                    fflush(stderr);
                    break;
                }
            }
""",
            ),
            (
                """        if (hint && hint->block && hint->type == BTYPE_MAP64 && hint->maxfree && (hint->is32bits==is32bits)) {
""",
                """        if (hint && hint->block && hint->type == BTYPE_MAP64 && hint->maxfree && (hint->is32bits==is32bits)) {
            // BOX64-BUILD: map-prot-probe hint 路径全块逐页 prot+vma 检查（抓中间页 PROT_NONE/消失）
            for(uintptr_t pg=(uintptr_t)hint->block; pg<(uintptr_t)hint->block+hint->size; pg+=0x1000) {
                if(!probe_vma((void*)pg) || !(getProtection(pg)&PROT_READ)) {
                    fprintf(stderr, "[map64probe] badpage=%p prot=%d vma=%d hint=%p block=%p first=%p lowest=%u n=%d last=%d\\n",
                        (void*)pg, getProtection(pg), probe_vma((void*)pg),
                        hint, hint->block, hint->first, hint->lowest, n_blocks, last_block_index_map64);
                    fflush(stderr);
                    break;
                }
            }
""",
            ),
            (
                """        rb_set(mapallmem, addr, addr+size, MEM_EXTERNAL);
""",
                """        // BOX64-BUILD: map-prot-probe EXTERNAL+PROT_NONE 段入簿时间线（32位，抓野指针段来源）
        if(box64_is32bits && prot==0)
            fprintf(stderr, "[lmprobe] ext-none=%p-%p\\n", (void*)addr, (void*)(addr+size));
        rb_set(mapallmem, addr, addr+size, MEM_EXTERNAL);
""",
            ),
            (
                """    if(is32bits) mutex_lock(&mutex_blocks);
    #ifdef TRACE_MEMSTAT
    customMalloc_allocated += allocsize;
    #endif
    size_t mapsize = (allocsize/128)/8;
""",
                """    if(is32bits) mutex_lock(&mutex_blocks);
    // BOX64-BUILD: map-prot-probe new128 高区/MAP_FAILED 现场（写条目前，抓 is32bits=1 拿到高区泄漏条目）
    if(p == MAP_FAILED || (is32bits && p && (uintptr_t)p > 0xffffffffULL))
        fprintf(stderr, "[new128hi] p=%p is32=%d i=%d\\n", p, is32bits, i);
    #ifdef TRACE_MEMSTAT
    customMalloc_allocated += allocsize;
    #endif
    size_t mapsize = (allocsize/128)/8;
""",
            ),
            (
                """    if(is32bits) mutex_lock(&mutex_blocks);

    #ifdef TRACE_MEMSTAT
    customMalloc_allocated += allocsize;
    #endif
""",
                """    if(is32bits) mutex_lock(&mutex_blocks);
    // BOX64-BUILD: map-prot-probe new64 高区/MAP_FAILED 现场（写条目前，抓 is32bits=1 拿到高区泄漏条目）
    if(p == MAP_FAILED || (is32bits && p && (uintptr_t)p > 0xffffffffULL))
        fprintf(stderr, "[new64hi] p=%p is32=%d i=%d\\n", p, is32bits, i);

    #ifdef TRACE_MEMSTAT
    customMalloc_allocated += allocsize;
    #endif
""",
            ),
            (
                """EXPORT int box_munmap(void* addr, size_t length)
{
    int ret = InternalMunmap(addr, length);
""",
                """EXPORT int box_munmap(void* addr, size_t length)
{
    int ret = InternalMunmap(addr, length);
    // BOX64-BUILD: map-prot-probe box_munmap 高区时间线（guest my_munmap 链走此，非 EXPORT munmap）
    if(box64_is32bits && !ret && (uintptr_t)addr > 0xffffffffULL)
        fprintf(stderr, "[boxmapprobe] hi-unmap addr=%p len=%lu inpool=%d caller=%p caller2=%p\\n",
            addr, length, findBlock((uintptr_t)addr)?1:0,
            __builtin_return_address(0), __builtin_return_address(1));
""",
            ),
            (
                """            int isReserved = box64_is32bits && (uintptr_t)addr>0xffffffffLL;
            InternalMunmap(addr, size);
""",
                """            int isReserved = box64_is32bits && (uintptr_t)addr>0xffffffffLL;
            // BOX64-BUILD: map-prot-probe DelMmaplist 高区 munmap+PROT_NONE re-reserve 时间线
            if(box64_is32bits && (uintptr_t)addr > 0xffffffffULL)
                fprintf(stderr, "[dellistprobe] hi-unmap addr=%p len=%lu inpool=%d caller=%p caller2=%p\\n",
                    addr, size, findBlock((uintptr_t)addr)?1:0,
                    __builtin_return_address(0), __builtin_return_address(1));
            InternalMunmap(addr, size);
""",
            ),
        ],
    ),
    (
        "src/custommmap.c",
        [
            (
                """#include <unistd.h>
#include <stdint.h>
""",
                """#include <unistd.h>
#include <stdint.h>
// BOX64-BUILD: map-prot-probe 探针 fprintf 需 stdio
#include <stdio.h>
""",
            ),
            (
                """    int ret = InternalMunmap(addr, length);
    if(!ret && mapallmem) {
""",
                """    int ret = InternalMunmap(addr, length);
    // BOX64-BUILD: map-prot-probe 32位进程高区 munmap 时间线（宿主池页消失指纹）
    // inpool 反查 findBlock（跨文件 extern，返回指针只测空）
    extern void* findBlock(uintptr_t addr);
    if(box64_is32bits && !ret && (uintptr_t)addr > 0xffffffffULL)
        fprintf(stderr, "[munmapprobe] hi-unmap addr=%p len=%lu inpool=%d caller=%p caller2=%p\\n", addr, length, findBlock((uintptr_t)addr)?1:0, __builtin_return_address(0), __builtin_return_address(1));
    if(!ret && mapallmem) {
""",
            ),
        ],
    ),
    (
        "src/wrapped/wrappedlibc.c",
        [
            (
                """EXPORT int32_t my_open(x64emu_t* emu, void* pathname, int32_t flags, uint32_t mode)
{
""",
                """// BOX64-BUILD: map-prot-probe open 前 path 指针页有效性（崩溃 A：野 path 进 isProcSelf/strncmp 崩）
static int probe_pathok(const void* p)
{
    unsigned char vec = 0;
    if(!p) return 0;
    if(mincore((void*)(((uintptr_t)p) & ~(uintptr_t)0xfff), 0x1000, &vec) == -1 && errno == ENOMEM) {
        fprintf(stderr, "[openprobe] bad-path=%p\\n", p);
        fflush(stderr);
        return 0;
    }
    return 1;
}
EXPORT int32_t my_open(x64emu_t* emu, void* pathname, int32_t flags, uint32_t mode)
{
    // BOX64-BUILD: map-prot-probe 野 path 防御（EFAULT 返回，跳过 pathmap/isProcSelf 后续读）
    if(pathname && !probe_pathok(pathname)) {
        errno = EFAULT;
        return -1;
    }
""",
            ),
        ],
    ),
    (
        "src/mallochook.c",
        [
            (
                """EXPORT void free(void* p)
{
""",
                """EXPORT void free(void* p)
{
    // BOX64-BUILD: map-prot-probe 高区 free 来源追踪（128K map64 块被 __libc_free 打洞）
    if(box64_is32bits && p && (uintptr_t)p > 0xffffffffULL)
        fprintf(stderr, "[freeprobe] hi-free=%p caller=%p\\n", p, __builtin_return_address(0));
""",
            ),
        ],
    ),
]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    root = Path(sys.argv[1])
    for rel, edits in JOBS:
        path = root / rel
        if not path.exists():
            print(f"锚点缺失: {path}")
            return 1
        text = path.read_text(encoding="utf-8")
        if SENTINEL in text:
            print(f"已幂等: {rel}")
            continue
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                print(f"锚点异常 {rel}: 出现 {n} 次（期望 1）: {old[:60]!r}")
                return 1
            text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        print(f"已应用 {rel}: {len(edits)} 段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
