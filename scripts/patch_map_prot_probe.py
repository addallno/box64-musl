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
            // BOX64-BUILD: map-prot-probe hint 路径全块逐页 vma+prot 检查（vma=mincore；prot=簿记直读 rb_get）
            for(uintptr_t pg=(uintptr_t)hint->block; pg<(uintptr_t)hint->block+hint->size; pg+=0x1000) {
                int vma_ok = (probe_vma((void*)pg) == 0);
                uint32_t pr = memprot ? rb_get(memprot, pg) : 0;
                if(!vma_ok || !(pr & PROT_READ)) {
                    static int bad_cnt = 0;
                    if(bad_cnt < 40) {
                        ++bad_cnt;
                        fprintf(stderr, "[map128probe] badpage=%p vma=%d prot=0x%x hint=%p block=%p first=%p lowest=%u n=%d last=%d\\n",
                            (void*)pg, vma_ok, pr, hint, hint->block, hint->first, hint->lowest, n_blocks, last_block_index_map128);
                        if(bad_cnt <= 3) {
                            FILE* mf = fopen("/proc/self/maps", "r");
                            if(mf) {
                                char ln[320];
                                uintptr_t bb = (uintptr_t)hint->block;
                                while(fgets(ln, sizeof(ln), mf)) {
                                    uintptr_t a = strtoul(ln, NULL, 16);
                                    char* dash = strchr(ln, '-');
                                    if(dash) {
                                        uintptr_t e = strtoul(dash+1, NULL, 16);
                                        if(bb >= a && bb < e) { fprintf(stderr, "[badmaps128] %s", ln); break; }
                                    }
                                }
                                fclose(mf);
                            }
                        }
                        fflush(stderr);
                    }
                    if(!vma_ok) {
                        mutex_unlock(&mutex_blocks);
                        return NULL;
                    }
                }
                // BOX64-BUILD: map-prot-probe hint128 崩前状态快照（first 被踩/prot 突变抓手）
                {
                    static int bh128 = 0;
                    if(bh128 < 20) {
                        ++bh128;
                        fprintf(stderr, "[hint128state] hint=%p block=%p first=%p sz=%lu lowest=%u maxfree=%u mapvma=%d mapprot=0x%x\\n",
                            hint, hint->block, hint->first, (unsigned long)hint->size, hint->lowest, hint->maxfree,
                            probe_vma(hint->first)==0, memprot ? rb_get(memprot,(uintptr_t)hint->first) : 0);
                        fflush(stderr);
                    }
                }
            }
""",
            ),
            (
                """        if (hint && hint->block && hint->type == BTYPE_MAP64 && hint->maxfree && (hint->is32bits==is32bits)) {
""",
                """        if (hint && hint->block && hint->type == BTYPE_MAP64 && hint->maxfree && (hint->is32bits==is32bits)) {
            // BOX64-BUILD: map-prot-probe hint 路径全块逐页 vma+prot 检查（vma=mincore；prot=簿记直读 rb_get）
            for(uintptr_t pg=(uintptr_t)hint->block; pg<(uintptr_t)hint->block+hint->size; pg+=0x1000) {
                int vma_ok = (probe_vma((void*)pg) == 0);
                uint32_t pr = memprot ? rb_get(memprot, pg) : 0;
                if(!vma_ok || !(pr & PROT_READ)) {
                    static int bad_cnt = 0;
                    if(bad_cnt < 40) {
                        ++bad_cnt;
                        fprintf(stderr, "[map64probe] badpage=%p vma=%d prot=0x%x hint=%p block=%p first=%p lowest=%u n=%d last=%d\\n",
                            (void*)pg, vma_ok, pr, hint, hint->block, hint->first, hint->lowest, n_blocks, last_block_index_map64);
                        if(bad_cnt <= 3) {
                            FILE* mf = fopen("/proc/self/maps", "r");
                            if(mf) {
                                char ln[320];
                                uintptr_t bb = (uintptr_t)hint->block;
                                while(fgets(ln, sizeof(ln), mf)) {
                                    uintptr_t a = strtoul(ln, NULL, 16);
                                    char* dash = strchr(ln, '-');
                                    if(dash) {
                                        uintptr_t e = strtoul(dash+1, NULL, 16);
                                        if(bb >= a && bb < e) { fprintf(stderr, "[badmaps64] %s", ln); break; }
                                    }
                                }
                                fclose(mf);
                            }
                        }
                        fflush(stderr);
                    }
                    if(!vma_ok) {
                        mutex_unlock(&mutex_blocks);
                        return NULL;
                    }
                }
                // BOX64-BUILD: map-prot-probe hint64 崩前状态快照（first 被踩/prot 突变抓手）
                {
                    static int bh64 = 0;
                    if(bh64 < 20) {
                        ++bh64;
                        fprintf(stderr, "[hint64state] hint=%p block=%p first=%p sz=%lu lowest=%u maxfree=%u mapvma=%d mapprot=0x%x\\n",
                            hint, hint->block, hint->first, (unsigned long)hint->size, hint->lowest, hint->maxfree,
                            probe_vma(hint->first)==0, memprot ? rb_get(memprot,(uintptr_t)hint->first) : 0);
                        fflush(stderr);
                    }
                }
            }
""",
            ),
            (
                """            uint16_t* map = p_blocks[i].first;
            uint32_t slices = p_blocks[i].size >> 6;
            for (uint32_t idx = p_blocks[i].lowest; idx < slices; ++idx) {
""",
                """            // BOX64-BUILD: map-prot-probe 主循环>4G块硬件 maps perms 快照（判簿记 vs 硬件脱钩）
            if((uintptr_t)p_blocks[i].block > 0xffffffffUL) {
                static int mb64 = 0;
                if(mb64 < 16) {
                    ++mb64;
                    fprintf(stderr, "[block64maps] i=%d block=%p first=%p sz=%u lowest=%u maxfree=%u\\n",
                        i, p_blocks[i].block, p_blocks[i].first, p_blocks[i].size, p_blocks[i].lowest, p_blocks[i].maxfree);
                    FILE* bmf = fopen("/proc/self/maps", "r");
                    if(bmf) {
                        char bln[320];
                        uintptr_t bb = (uintptr_t)p_blocks[i].block;
                        while(fgets(bln, sizeof(bln), bmf)) {
                            uintptr_t a = strtoul(bln, NULL, 16);
                            char* dash = strchr(bln, '-');
                            if(dash) {
                                uintptr_t e = strtoul(dash+1, NULL, 16);
                                if(bb >= a && bb < e) { fprintf(stderr, "[block64maps] %s", bln); break; }
                            }
                        }
                        fclose(bmf);
                    }
                    fflush(stderr);
                }
            }
            // BOX64-BUILD: map-prot-probe 主循环块健康检查（坏块跳过，防崩）
            {
                int bad_blk = 0;
                for(uintptr_t pg=(uintptr_t)p_blocks[i].block; pg<(uintptr_t)p_blocks[i].block+p_blocks[i].size; pg+=0x1000) {
                    int vma_ok = (probe_vma((void*)pg) == 0);
                    uint32_t pr = memprot ? rb_get(memprot, pg) : 0;
                    if(!vma_ok || !(pr & PROT_READ)) {
                        static int bad_cnt_m64 = 0;
                        if(bad_cnt_m64 < 40) {
                            ++bad_cnt_m64;
                            fprintf(stderr, "[map64mprobe] badpage=%p vma=%d prot=0x%x i=%d block=%p first=%p\\n",
                                (void*)pg, vma_ok, pr, i, p_blocks[i].block, p_blocks[i].first);
                            if(bad_cnt_m64 <= 3) {
                                FILE* mf = fopen("/proc/self/maps", "r");
                                if(mf) {
                                    char ln[320];
                                    uintptr_t bb = (uintptr_t)p_blocks[i].block;
                                    while(fgets(ln, sizeof(ln), mf)) {
                                        uintptr_t a = strtoul(ln, NULL, 16);
                                        char* dash = strchr(ln, '-');
                                        if(dash) {
                                            uintptr_t e = strtoul(dash+1, NULL, 16);
                                            if(bb >= a && bb < e) { fprintf(stderr, "[badmaps64m] %s", ln); break; }
                                        }
                                    }
                                    fclose(mf);
                                }
                            }
                            fflush(stderr);
                        }
                        if(!vma_ok) {
                            bad_blk = 1;
                            break;
                        }
                    }
                }
                if(bad_blk) continue;
            }
            uint16_t* map = p_blocks[i].first;
            uint32_t slices = p_blocks[i].size >> 6;
            for (uint32_t idx = p_blocks[i].lowest; idx < slices; ++idx) {
""",
            ),
            (
                """            // look for a free block
            uint8_t* map = p_blocks[i].first;
""",
                """            // BOX64-BUILD: map-prot-probe 主循环块健康检查（坏块跳过，防崩）
            {
                int bad_blk = 0;
                for(uintptr_t pg=(uintptr_t)p_blocks[i].block; pg<(uintptr_t)p_blocks[i].block+p_blocks[i].size; pg+=0x1000) {
                    int vma_ok = (probe_vma((void*)pg) == 0);
                    uint32_t pr = memprot ? rb_get(memprot, pg) : 0;
                    if(!vma_ok || !(pr & PROT_READ)) {
                        static int bad_cnt_m128 = 0;
                        if(bad_cnt_m128 < 40) {
                            ++bad_cnt_m128;
                            fprintf(stderr, "[map128mprobe] badpage=%p vma=%d prot=0x%x i=%d block=%p first=%p\\n",
                                (void*)pg, vma_ok, pr, i, p_blocks[i].block, p_blocks[i].first);
                            if(bad_cnt_m128 <= 3) {
                                FILE* mf = fopen("/proc/self/maps", "r");
                                if(mf) {
                                    char ln[320];
                                    uintptr_t bb = (uintptr_t)p_blocks[i].block;
                                    while(fgets(ln, sizeof(ln), mf)) {
                                        uintptr_t a = strtoul(ln, NULL, 16);
                                        char* dash = strchr(ln, '-');
                                        if(dash) {
                                            uintptr_t e = strtoul(dash+1, NULL, 16);
                                            if(bb >= a && bb < e) { fprintf(stderr, "[badmaps128m] %s", ln); break; }
                                        }
                                    }
                                    fclose(mf);
                                }
                            }
                            fflush(stderr);
                        }
                        if(!vma_ok) {
                            bad_blk = 1;
                            break;
                        }
                    }
                }
                if(bad_blk) continue;
            }
            // look for a free block
            uint8_t* map = p_blocks[i].first;
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
            (
                """        } else
            setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
    }
    return ret;
}
// the BTYPE_MAP64 is a simple bitmap based allocator""",
                """        } else
            setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
    }
    // BOX64-BUILD: map-prot-probe newBlock setProtection/defer 状态（单槽 defer 覆盖丢失=prot0 抓手）
    {
        uint32_t pr_end = 0; uintptr_t pr_bend = 0;
        int pr_has = memprot ? rb_get_end(memprot, (uintptr_t)p, &pr_end, &pr_bend) : 0;
        fprintf(stderr, "[setprot128] p=%p sz=%lu setting=%d defered_p=%p dprot=0x%x nowprot=0x%x endprot=0x%x hasend=%d bend=%p\\n",
            (void*)p, (unsigned long)allocsize, setting_prot, (void*)defered_prot_p,
            defered_prot_prot, memprot ? rb_get(memprot, (uintptr_t)p) : 0, pr_end, pr_has, (void*)pr_bend);
    }
    fflush(stderr);
    return ret;
}
// the BTYPE_MAP64 is a simple bitmap based allocator""",
            ),
            (
                """        } else {
            setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
        }
    }

    return ret;
}""",
                """        } else {
            setProtection((uintptr_t)p, allocsize, PROT_READ | PROT_WRITE);
        }
    }
    // BOX64-BUILD: map-prot-probe newBlock setProtection/defer 状态（单槽 defer 覆盖丢失=prot0 抓手）
    {
        uint32_t pr_end = 0; uintptr_t pr_bend = 0;
        int pr_has = memprot ? rb_get_end(memprot, (uintptr_t)p, &pr_end, &pr_bend) : 0;
        fprintf(stderr, "[setprot64] p=%p sz=%lu setting=%d defered_p=%p dprot=0x%x nowprot=0x%x endprot=0x%x hasend=%d bend=%p\\n",
            (void*)p, (unsigned long)allocsize, setting_prot, (void*)defered_prot_p,
            defered_prot_prot, memprot ? rb_get(memprot, (uintptr_t)p) : 0, pr_end, pr_has, (void*)pr_bend);
    }
    fflush(stderr);

    return ret;
}""",
            ),
            (
                """    dynarec_log(LOG_DEBUG, "freeProtection %p:%p\\n", (void*)addr, (void*)(addr+size-1));
    LOCK_PROT();
    rb_unset(mapallmem, addr, addr+size);
    rb_unset(memprot, addr, addr+size);
""",
                """    dynarec_log(LOG_DEBUG, "freeProtection %p:%p\\n", (void*)addr, (void*)(addr+size-1));
    // BOX64-BUILD: map-prot-probe memprot 清除时间线（>4G 池块簿记 prot=0 元凶抓手）
    if(box64_is32bits && addr>0xffffffffULL) {
        static int mu_f = 0;
        if(mu_f < 32) { ++mu_f; fprintf(stderr, "[memunfree] addr=%p sz=%lu caller=%p\\n", (void*)addr, (unsigned long)size, __builtin_return_address(0)); fflush(stderr); }
    }
    LOCK_PROT();
    rb_unset(mapallmem, addr, addr+size);
    rb_unset(memprot, addr, addr+size);
""",
            ),
            (
                """    if(!prot) {
        LOCK_PROT();
        rb_set(mapallmem, addr, addr+size, flag);
        rb_unset(memprot, addr, addr+size);
        UNLOCK_PROT();
    }
    else{//SetProtection
""",
                """    if(!prot) {
        // BOX64-BUILD: map-prot-probe memprot 清除时间线（setProtection_generic prot=0）
        if(box64_is32bits && addr>0xffffffffULL) {
            static int mu_g = 0;
            if(mu_g < 32) { ++mu_g; fprintf(stderr, "[memungen0] addr=%p sz=%lu\\n", (void*)addr, (unsigned long)size); fflush(stderr); }
        }
        LOCK_PROT();
        rb_set(mapallmem, addr, addr+size, flag);
        rb_unset(memprot, addr, addr+size);
        UNLOCK_PROT();
    }
    else{//SetProtection
""",
            ),
            (
                """    if(prot)
        setProtection_generic(addr, size, prot, MEM_ELF);
    else {
        LOCK_PROT();
        rb_set(mapallmem, addr, addr+size, MEM_ELF);
        rb_unset(memprot, addr, addr+size);
        UNLOCK_PROT();
    }
""",
                """    if(prot)
        setProtection_generic(addr, size, prot, MEM_ELF);
    else {
        // BOX64-BUILD: map-prot-probe memprot 清除时间线（setProtection_elf prot=0）
        if(box64_is32bits && addr>0xffffffffULL) {
            static int mu_e = 0;
            if(mu_e < 32) { ++mu_e; fprintf(stderr, "[memunelf] addr=%p sz=%lu\\n", (void*)addr, (unsigned long)size); fflush(stderr); }
        }
        LOCK_PROT();
        rb_set(mapallmem, addr, addr+size, MEM_ELF);
        rb_unset(memprot, addr, addr+size);
        UNLOCK_PROT();
    }
""",
            ),
    (
        """void setProtection(uintptr_t addr, size_t size, uint32_t prot)
{
    size = ALIGN(size);
    LOCK_PROT();
    ++setting_prot;
    uintptr_t cur = ALIGN_DOWN(addr);
    uintptr_t end = ALIGN(cur+size);
    rb_set(mapallmem, cur, end, MEM_ALLOCATED);
    rb_set(memprot, cur, end, prot);
    --setting_prot;
    UNLOCK_PROT();
}
""",
        """void setProtection(uintptr_t addr, size_t size, uint32_t prot)
{
    size = ALIGN(size);
    LOCK_PROT();
    ++setting_prot;
    uintptr_t cur = ALIGN_DOWN(addr);
    uintptr_t end = ALIGN(cur+size);
    int rs1 = rb_set(mapallmem, cur, end, MEM_ALLOCATED);
    int rs2 = rb_set(memprot, cur, end, prot);
    // BOX64-BUILD: map-prot-probe setProtection rb_set 返回值（>4G 128K池块，抓写入丢失/截断）
    if(box64_is32bits && cur > 0xffffffffULL && size <= 0x20000) {
        static int sp_n = 0;
        if(sp_n < 32) {
            uint32_t v = 0; uintptr_t b = 0;
            int h = rb_get_end(memprot, cur, &v, &b);
            ++sp_n;
            fprintf(stderr, "[setp64chk] cur=%p end=%p prot=0x%x rs1=%d rs2=%d hasend=%d v=0x%x b=%p\\n",
                (void*)cur, (void*)end, prot, rs1, rs2, h, v, (void*)b);
            fflush(stderr);
        }
    }
    --setting_prot;
    UNLOCK_PROT();
}
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
