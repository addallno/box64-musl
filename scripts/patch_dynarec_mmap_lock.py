#!/usr/bin/env python3
"""patch_dynarec_mmap_lock.py — box32_dynarec_mmap static cur 加锁（崩溃 B 修复）

背景（崩溃 B，probe6-10 归因）：
- 32 位进程 64 位块（map128/map64 池）走 box32_dynarec_mmap(128K)
- 该函数 static uintptr_t cur 无锁：多线程同时进入→同一 RESERVED 段
  MAP_FIXED RWX 重叠分配→后创建块覆盖先创建块内容/簿记错乱
- 且成功路径 `//rb_set MEM_BOX` 被注释=簿记不更新，重叠无任何防护
- 崩溃指纹：map64/map128 hint 循环读位图时页不存在（prot=0, ERR=5），
  所有 munmap 通道探针（EXPORT munmap/free/box_munmap/DelMmaplist）全 0

修复：函数加 pthread mutex（覆盖 static cur 读改写全程），防重叠 MAP_FIXED。
不改簿记（rb_set MEM_BOX 保持注释，避免引入簿记一致性风险）。

哨兵：BOX64-BUILD: dynarec-mmap-lock（幂等）
用法：python3 patch_dynarec_mmap_lock.py <box64源码目录>
退出码：0=成功/已幂等，1=锚点缺失
"""
import sys
from pathlib import Path

SENTINEL = "BOX64-BUILD: dynarec-mmap-lock"

OLD = """void* box32_dynarec_mmap(size_t size, int fd, off_t offset)
{
    #ifdef BOX32
    // find a block that was prereserve before and big enough
    size = ALIGN(size);
    uint32_t flag;
    static uintptr_t cur = 0x100000000LL;
    uintptr_t bend = 0;
    while(bend<0x800000000000LL) {
        uint32_t map_flags = MAP_FIXED | ((fd==-1)?MAP_ANONYMOUS:0) | MAP_PRIVATE;
        if(rb_get_end(mapallmem, cur, &flag, &bend)) {
            if(flag == MEM_RESERVED && bend-cur>=size) {
                void* ret = InternalMmap((void*)cur, size, PROT_READ | PROT_WRITE | PROT_EXEC, map_flags, fd, offset);
                if(ret!=MAP_FAILED) {
                    //rb_set(mapallmem, cur, cur+size, MEM_BOX);    // mark as allocated by/for box
                } else
                    printf_log(LOG_INFO, "Error allocating Dynarec memory: %s\\n", strerror(errno));
                cur = cur+size;
                return ret;
            }
        }
        cur = bend;
    }
#endif
"""

NEW = """void* box32_dynarec_mmap(size_t size, int fd, off_t offset)
{
    #ifdef BOX32
    // find a block that was prereserve before and big enough
    size = ALIGN(size);
    uint32_t flag;
    static uintptr_t cur = 0x100000000LL;
    // BOX64-BUILD: dynarec-mmap-lock static cur 多线程重叠 MAP_FIXED 修复（崩溃 B）
    static pthread_mutex_t cur_mutex = PTHREAD_MUTEX_INITIALIZER;
    pthread_mutex_lock(&cur_mutex);
    uintptr_t bend = 0;
    while(bend<0x800000000000LL) {
        uint32_t map_flags = MAP_FIXED | ((fd==-1)?MAP_ANONYMOUS:0) | MAP_PRIVATE;
        if(rb_get_end(mapallmem, cur, &flag, &bend)) {
            if(flag == MEM_RESERVED && bend-cur>=size) {
                void* ret = InternalMmap((void*)cur, size, PROT_READ | PROT_WRITE | PROT_EXEC, map_flags, fd, offset);
                if(ret!=MAP_FAILED) {
                    //rb_set(mapallmem, cur, cur+size, MEM_BOX);    // mark as allocated by/for box
                } else
                    printf_log(LOG_INFO, "Error allocating Dynarec memory: %s\\n", strerror(errno));
                cur = cur+size;
                pthread_mutex_unlock(&cur_mutex);
                return ret;
            }
        }
        cur = bend;
    }
    pthread_mutex_unlock(&cur_mutex);
#endif
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    root = Path(sys.argv[1])
    path = root / "src/custommem.c"
    if not path.exists():
        print(f"锚点缺失: {path}")
        return 1
    text = path.read_text(encoding="utf-8")
    if SENTINEL in text:
        print("已幂等: src/custommem.c")
        return 0
    n = text.count(OLD)
    if n != 1:
        print(f"锚点异常 src/custommem.c: 出现 {n} 次（期望 1）")
        return 1
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("已应用 src/custommem.c: 1 段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
