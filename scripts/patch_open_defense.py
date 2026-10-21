#!/usr/bin/env python3
# 崩溃 A 永久防御（自 patch_map_prot_probe.py 剥离，不受 PROBE 开关影响）：
# guest int80 sys_open 的 pathname 指针所在页不存在时，原链路
# my_open→box64_pathmap/isProcSelf→strncmp 野读 SIGSEGV。
# 防御：open 前 mincore 检查 path 指针页，不存在则 EFAULT 返回。
# 哨兵：BOX64-BUILD: open-defense（幂等）
import sys
from pathlib import Path

SENTINEL = "BOX64-BUILD: open-defense"

JOBS = [
    (
        "src/wrapped/wrappedlibc.c",
        [
            (
                """EXPORT int32_t my_open(x64emu_t* emu, void* pathname, int32_t flags, uint32_t mode)
{
""",
                """// BOX64-BUILD: open-defense open 前 path 指针页有效性（崩溃 A：野 path 进 isProcSelf/strncmp 崩）
static int open_defense_pathok(const void* p)
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
    // BOX64-BUILD: open-defense 野 path 防御（EFAULT 返回，跳过 pathmap/isProcSelf 后续读）
    if(pathname && !open_defense_pathok(pathname)) {
        errno = EFAULT;
        return -1;
    }
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
        print("用法: patch_open_defense.py <box64源码目录>")
        sys.exit(2)
    root = Path(sys.argv[1])
    for rel, edits in JOBS:
        if not apply_file(root / rel, edits):
            sys.exit(1)
    print("open-defense 完成")


if __name__ == "__main__":
    main()
