#!/usr/bin/env python3
"""patch_getdb_probe.py — getDBSize 槽值/垃圾 db 探测+防御（rc=139 根因修复）

问题（2026-10-07 崩溃取证）：
- getDBSize 头部 `*db = *(dynablock_t**)(entry-8)` 无条件解引用槽值；
  槽=native_next（dynarec 小枚举，非指针）或 dlclose 后被重置的值时，
  entry-8 读出垃圾 db 非 NULL → 提前 return 垃圾 → 唯一调用方
  cleanDBFromAddressRange(destroy=1) → FreeRangeDynablock 解引用野指针
  SIGSEGV @0x34ab19e4（rc=139，集中发生在 Unloading Steam API 卸载期
  setJumpTableDefault64 重置槽之后；历史即存在，非新补丁引入）。
- 尾部 `*db = *(block[idx0]-8)`（native_next while 循环后）同样无防御。

修复：
- entry < 0x400000 或非 8 对齐（小枚举/0/野值）→ 不读 -8，*db=NULL，
  落到后续 default 检查 / native_next while 循环（原版预期路径）；
- 读出的 db < 0x400000 或非 8 对齐（垃圾）→ fprintf 取证日志 + *db=NULL。

哨兵：BOX64-BUILD: getDBSize-badprobe（幂等）
依赖：patch_jmptbl_acquire.py 先执行（锚为 acquire 应用后形态）
用法：python3 patch_getdb_probe.py <box64源码目录>
退出码：0=成功/已幂等，1=锚点缺失
"""
import sys
from pathlib import Path

SENTINEL = "BOX64-BUILD: getDBSize-badprobe"

# (相对路径, 锚文本, 替换文本)
JOBS = [
    # ---- 1) getDBSize 头部：entry 合法性 probe + 垃圾 db 防御 ----
    (
        "src/custommem.c",
        """    #ifdef JMPTABL_START4
    // BOX64-BUILD: jmptbl-acquire entry acquire 后再取 -8 处的 db 指针
    {
        uintptr_t entry = (uintptr_t)__atomic_load_n(&box64_jmptbl3[idx3][idx2][idx1][idx0], __ATOMIC_ACQUIRE);
        *db = *(dynablock_t**)(entry - sizeof(void*));
    }
    #else
    {
        uintptr_t entry = (uintptr_t)__atomic_load_n(&box64_jmptbl2[idx2][idx1][idx0], __ATOMIC_ACQUIRE);
        *db = *(dynablock_t**)(entry - sizeof(void*));
    }
    #endif
""",
        """    #ifdef JMPTABL_START4
    // BOX64-BUILD: jmptbl-acquire entry acquire 后再取 -8 处的 db 指针
    {
        uintptr_t entry = (uintptr_t)__atomic_load_n(&box64_jmptbl3[idx3][idx2][idx1][idx0], __ATOMIC_ACQUIRE);
        // BOX64-BUILD: getDBSize-badprobe 槽值非合法指针（native_next 小枚举等）不读 -8，走 default/native_next 路径
        *db = (entry >= 0x400000 && !(entry & 7)) ? *(dynablock_t**)(entry - sizeof(void*)) : NULL;
        if(*db && (((uintptr_t)*db < 0x400000) || ((uintptr_t)*db & 7))) {
            fprintf(stderr, "[box64] getDBSize-badprobe head bad db=%p entry=%p addr=%p idx0=%d\\n",
                (void*)*db, (void*)entry, (void*)addr, (int)idx0);
            *db = NULL;
        }
    }
    #else
    {
        uintptr_t entry = (uintptr_t)__atomic_load_n(&box64_jmptbl2[idx2][idx1][idx0], __ATOMIC_ACQUIRE);
        // BOX64-BUILD: getDBSize-badprobe
        *db = (entry >= 0x400000 && !(entry & 7)) ? *(dynablock_t**)(entry - sizeof(void*)) : NULL;
        if(*db && (((uintptr_t)*db < 0x400000) || ((uintptr_t)*db & 7))) {
            fprintf(stderr, "[box64] getDBSize-badprobe head bad db=%p entry=%p addr=%p idx0=%d\\n",
                (void*)*db, (void*)entry, (void*)addr, (int)idx0);
            *db = NULL;
        }
    }
    #endif
""",
    ),
    # ---- 2) getDBSize 尾部：while 后的 -8 读同样防御 ----
    (
        "src/custommem.c",
        """    *db = *(dynablock_t**)(block[idx0]- sizeof(void*));
    return (addr&~JMPTABLE_MASK0)+idx0+1;
""",
        """    // BOX64-BUILD: getDBSize-badprobe 尾部 -8 读同样 probe
    {
        uintptr_t entry = block[idx0];
        dynablock_t* found = (entry >= 0x400000 && !(entry & 7)) ? *(dynablock_t**)(entry - sizeof(void*)) : NULL;
        if(found && (((uintptr_t)found < 0x400000) || ((uintptr_t)found & 7))) {
            fprintf(stderr, "[box64] getDBSize-badprobe tail bad db=%p entry=%p addr=%p idx0=%d\\n",
                (void*)found, (void*)entry, (void*)addr, (int)idx0);
            found = NULL;
        }
        *db = found;
    }
    return (addr&~JMPTABLE_MASK0)+idx0+1;
""",
    ),
]


def main() -> None:
    if len(sys.argv) != 2:
        print("用法: patch_getdb_probe.py <box64源码目录>", file=sys.stderr)
        sys.exit(2)
    srcdir = Path(sys.argv[1])
    for i, (rel, anchor, repl) in enumerate(JOBS, 1):
        p = srcdir / rel
        if not p.is_file():
            print(f"文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
        s = p.read_text(encoding="utf-8")
        if anchor in s:
            s = s.replace(anchor, repl, 1)
            p.write_text(s, encoding="utf-8")
            print(f"patch_getdb_probe: {rel} job{i} 已打补丁")
        elif repl in s:
            print(f"patch_getdb_probe: {rel} job{i} 已应用过，跳过")
        else:
            print(f"patch_getdb_probe: {rel} job{i} 锚点缺失", file=sys.stderr)
            sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
