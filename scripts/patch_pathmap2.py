#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pathmap2：补齐 wrapped 直通函数的 BOX64_PATHMAP 映射（增量，独立哨兵）。

背景：动态 guest（如 steamcmd 的 breakpad）调 access/__xstat64/mkdir 时，
- __xstat 系列虽是 GOM(my_ 实现) 但实现体内未插 pathmap；
- mkdir/access 是 GOW/GO native 直通，完全绕过映射。
两者在无 /tmp 的宿主（Termux）上全 ENOENT → breakpad Fatal assert。
32 位 guest 走独立的 wrapped32/my32_* 集，必须同样补（否则 box32 下
steamcmd 断言 "Please delete some /tmp/dumps*" 必现）。

改动：
  wrapped/wrappedlibc.c：
    1) my___xstat / my___lxstat / my___fxstatat 函数体补 box64_pathmap；
    2) 新增 my_mkdir / my_access / my_mkdirat（box64_pathmap + 转发宿主 libc）。
  wrapped/wrappedlibc_private.h：
    GOW(access, iFpi) → GOM(access, iFEpi)
    GOW(mkdir,  iFpu) → GOM(mkdir,  iFEpu)
    GO(mkdirat, iFipu) → GOM(mkdirat, iFEipii)
  wrapped32/（32位 guest）：
    1) my32___xstat / my32___lxstat / my32___lxstat64 / my32___fxstatat64 / my32_stat
       函数体补 box64_pathmap；
    2) 新增 my32_mkdir / my32_access / my32_mkdirat；
    3) private.h: GOW(access,iEpi)→GOM(iEEpi), GOW(mkdir,iEpu)→GOM(iEEpu),
       GO(mkdirat,iEipu)→GOM(iEEipii)（wrapped32 的 emu 槽为 iEE* 形态）。

两段各自独立哨兵跳过，可分别重入。
用法: patch_pathmap2.py <box64源码目录>
"""
import os
import sys

SENTINEL = "BOX64-BUILD: pathmap2"

NEW_FUNCS = (
    "// BOX64-BUILD: pathmap2 mkdir/access 路径映射（原为 native 直通）\n"
    "EXPORT int my_mkdir(x64emu_t* emu, const char* path, mode_t mode)\n"
    "{\n"
    "    (void)emu;\n"
    "    return mkdir((const char*)box64_pathmap(path), mode);\n"
    "}\n"
    "EXPORT int my_access(x64emu_t* emu, const char* path, int mode)\n"
    "{\n"
    "    (void)emu;\n"
    "    return access((const char*)box64_pathmap(path), mode);\n"
    "}\n"
    "EXPORT int my_mkdirat(x64emu_t* emu, int dirfd, const char* path, mode_t mode, int unused)\n"
    "{\n"
    "    (void)emu; (void)unused;\n"
    "    return mkdirat(dirfd, (const char*)box64_pathmap(path), mode);\n"
    "}\n"
)

# wrapped32 版：32位 guest 的 mkdir/access/mkdirat 路径映射
NEW_FUNCS32 = (
    "// BOX64-BUILD: pathmap2 32位 mkdir/access/mkdirat 路径映射（原为 native 直通）\n"
    "EXPORT int my32_mkdir(x64emu_t* emu, const char* path, mode_t mode)\n"
    "{\n"
    "    (void)emu;\n"
    "    const char* mp = box64_pathmap(path);\n"
    "    int r = mkdir(mp, mode);\n"
    "    printf_log(LOG_INFO, \"[p2dbg32] mkdir in=%p(%s) out=%p(%s) r=%d errno=%d\\n\",\n"
    "        path, path ? path : \"(null)\", mp, mp ? mp : \"(null)\", r, errno);\n"
    "    return r;\n"
    "}\n"
    "EXPORT int my32_access(x64emu_t* emu, const char* path, int mode)\n"
    "{\n"
    "    (void)emu;\n"
    "    return access((const char*)box64_pathmap(path), mode);\n"
    "}\n"
    "EXPORT int my32_mkdirat(x64emu_t* emu, int dirfd, const char* path, mode_t mode, int unused)\n"
    "{\n"
    "    (void)emu; (void)unused;\n"
    "    return mkdirat(dirfd, (const char*)box64_pathmap(path), mode);\n"
    "}\n"
)


def fail(msg):
    print(f"[patch_pathmap2] 失败: {msg}", file=sys.stderr)
    sys.exit(1)


def apply(src, old, new, want, name, fname):
    n = src.count(old)
    if n != want:
        fail(f"{fname} 锚点 [{name}] 命中 {n} 次（期望 {want}）: {old[:80]!r}")
    return src.replace(old, new)


def _patch64(srcdir):
    p_libc = os.path.join(srcdir, "src", "wrapped", "wrappedlibc.c")
    p_priv = os.path.join(srcdir, "src", "wrapped", "wrappedlibc_private.h")
    for p in (p_libc, p_priv):
        if not os.path.isfile(p):
            fail(f"文件不存在: {p}")

    with open(p_libc, "r", encoding="utf-8") as f:
        libc = f.read()
    with open(p_priv, "r", encoding="utf-8") as f:
        priv = f.read()

    if SENTINEL in libc and SENTINEL in priv:
        print("patch_pathmap2: wrapped 已应用过，跳过")
        return

    # ---- 1) stat 家族补 pathmap（锚点=签名+首行+`(void)`行，唯一） ----
    def stat_job(sig, var):
        head = sig + "{\n"
        old = head + "    (void)emu; (void)v;\n"
        new = (
            head
            + "    (void)emu; (void)v;\n"
            + f"    {var} = (void*)box64_pathmap((const char*){var});  // {SENTINEL}\n"
        )
        return (old, new)

    libc = apply(libc, *stat_job(
        "EXPORT int my___xstat(x64emu_t* emu, int v, void* path, void* buf)\n",
        "path"), 1, "my___xstat", "wrappedlibc.c")
    libc = apply(libc, *stat_job(
        "EXPORT int my___lxstat(x64emu_t* emu, int v, void* name, void* buf)\n",
        "name"), 1, "my___lxstat", "wrappedlibc.c")
    libc = apply(libc, *stat_job(
        "EXPORT int my___fxstatat(x64emu_t* emu, int v, int d, void* path, void* buf, int flags)\n",
        "path"), 1, "my___fxstatat", "wrappedlibc.c")

    # ---- 2) 新增 my_mkdir/my_access/my_mkdirat，插在 my_stat 定义前 ----
    anchor = "EXPORT int my_stat(x64emu_t *emu, void* filename, void* buf)\n{\n"
    libc = apply(libc, anchor, NEW_FUNCS + anchor, 1,
                 "my_stat 前插新函数", "wrappedlibc.c")

    # ---- 3) private.h：GOW/GO → GOM ----
    priv = apply(priv, "GOW(access, iFpi)\n", "GOM(access, iFEpi)   // BOX64-BUILD: pathmap2 GOM 非weak直查(919行)\n",
                 1, "GOW(access)", "wrappedlibc_private.h")
    priv = apply(priv, "GOW(mkdir, iFpu)\n", "GOM(mkdir, iFEpu)     // BOX64-BUILD: pathmap2 GOM 非weak直查(919行)\n",
                 1, "GOW(mkdir)", "wrappedlibc_private.h")
    # mkdirat：glibc 直通条目绕过 layer1 syscall hook（Breakpad dumps 目录创建失败根因）
    priv = apply(priv, "GO(mkdirat, iFipu)\n",
                 "GOM(mkdirat, iFEipii)  // BOX64-BUILD: pathmap2 at族GOM直查(919行)\n",
                 1, "GO(mkdirat)", "wrappedlibc_private.h")

    with open(p_libc, "w", encoding="utf-8") as f:
        f.write(libc)
    with open(p_priv, "w", encoding="utf-8") as f:
        f.write(priv)
    print("patch_pathmap2: 已应用 -> wrappedlibc.c(5处), wrappedlibc_private.h(3处)")


def _patch32(srcdir):
    # 32位 steamcmd breakpad 调 __xstat64/mkdir 走 wrapped32/my32_*，原无映射 → ENOENT Fatal
    p_libc32 = os.path.join(srcdir, "src", "wrapped32", "wrappedlibc.c")
    p_priv32 = os.path.join(srcdir, "src", "wrapped32", "wrappedlibc_private.h")
    for p in (p_libc32, p_priv32):
        if not os.path.isfile(p):
            fail(f"文件不存在: {p}")
    with open(p_libc32, "r", encoding="utf-8") as f:
        libc32 = f.read()
    with open(p_priv32, "r", encoding="utf-8") as f:
        priv32 = f.read()
    if SENTINEL in libc32 and SENTINEL in priv32:
        print("patch_pathmap2: wrapped32 已应用过，跳过")
        return

    # stat 家族：锚只要求签名+`{\n`（宽容锚，兼容上游 stat64/stat 两种体版本，
    # pathmap 行插在函数体首行，先于任何 stat 调用即可）
    def stat_job32(sig, var):
        head = sig + "{\n"
        new = head + f"    {var} = (void*)box64_pathmap((const char*){var});  // {SENTINEL}\n"
        return (head, new)

    libc32 = apply(libc32, *stat_job32(
        "EXPORT int my32___xstat(x64emu_t* emu, int v, void* path, void* buf)\n",
        "path"), 1, "my32___xstat", "wrapped32/wrappedlibc.c")
    libc32 = apply(libc32, *stat_job32(
        "EXPORT int my32___lxstat(x64emu_t* emu, int v, void* name, void* buf)\n",
        "name"), 1, "my32___lxstat", "wrapped32/wrappedlibc.c")
    libc32 = apply(libc32, *stat_job32(
        "EXPORT int my32___lxstat64(x64emu_t* emu, int v, void* name, void* buf)\n",
        "name"), 1, "my32___lxstat64", "wrapped32/wrappedlibc.c")
    libc32 = apply(libc32, *stat_job32(
        "EXPORT int my32___fxstatat64(x64emu_t* emu, int v, int d, void* path, void* buf, int flags)\n",
        "path"), 1, "my32___fxstatat64", "wrapped32/wrappedlibc.c")
    # my32_stat 无 emu 参数（GOM noE 特例）
    libc32 = apply(libc32,
        "EXPORT int my32_stat(char* path, void* buf)\n{\n",
        "EXPORT int my32_stat(char* path, void* buf)\n{\n"
        f"    path = (void*)box64_pathmap(path);  // {SENTINEL}\n",
        1, "my32_stat", "wrapped32/wrappedlibc.c")

    # 新增 my32_mkdir/my32_access/my32_mkdirat，插在 my32_stat 定义前
    anchor32 = "EXPORT int my32_stat(char* path, void* buf)\n{\n"
    libc32 = apply(libc32, anchor32, NEW_FUNCS32 + anchor32, 1,
                   "my32_stat 前插新函数", "wrapped32/wrappedlibc.c")

    # private.h：GOW/GO → GOM（wrapped32 的 emu 槽为 iEE* 形态）
    priv32 = apply(priv32, "GOW(access, iEpi)\n",
                   "GOM(access, iEEpi)    // BOX64-BUILD: pathmap2 wrapped32\n",
                   1, "GOW(access)", "wrapped32/wrappedlibc_private.h")
    priv32 = apply(priv32, "GOW(mkdir, iEpu)\n",
                   "GOM(mkdir, iEEpu)     // BOX64-BUILD: pathmap2 wrapped32\n",
                   1, "GOW(mkdir)", "wrapped32/wrappedlibc_private.h")
    priv32 = apply(priv32, "GO(mkdirat, iEipu)\n",
                   "GOM(mkdirat, iEEipii) // BOX64-BUILD: pathmap2 wrapped32\n",
                   1, "GO(mkdirat)", "wrapped32/wrappedlibc_private.h")

    # ---- 生成 iEEpu trampoline（CI 跳过 rebuild_wrappers_32，预生成版缺此条） ----
    p_w32c = os.path.join(srcdir, "src", "wrapped32", "generated", "wrapper32.c")
    p_w32h = os.path.join(srcdir, "src", "wrapped32", "generated", "wrapper32.h")
    for p in (p_w32c, p_w32h):
        if not os.path.isfile(p):
            fail(f"文件不存在: {p}")
    with open(p_w32c, "r", encoding="utf-8") as f:
        w32c = f.read()
    with open(p_w32h, "r", encoding="utf-8") as f:
        w32h = f.read()
    if "iEEpu_32" not in w32c:
        w32c = apply(w32c,
            "typedef int32_t (*iEEpi_t)(x64emu_t*, void*, int32_t);\n",
            "typedef int32_t (*iEEpi_t)(x64emu_t*, void*, int32_t);\n"
            "typedef int32_t (*iEEpu_t)(x64emu_t*, void*, uint32_t);\n",
            1, "iEEpi typedef", "wrapper32.c")
        w32c = apply(w32c,
            "void iEEpi_32(x64emu_t *emu, uintptr_t fcn) { iEEpi_t fn = (iEEpi_t)fcn; errno = emu->libc_err; R_EAX = fn(emu, from_ptriv(R_ESP + 4), from_ptri(int32_t, R_ESP + 8)); emu->libc_err = errno; }\n",
            "void iEEpi_32(x64emu_t *emu, uintptr_t fcn) { iEEpi_t fn = (iEEpi_t)fcn; errno = emu->libc_err; R_EAX = fn(emu, from_ptriv(R_ESP + 4), from_ptri(int32_t, R_ESP + 8)); emu->libc_err = errno; }\n"
            "void iEEpu_32(x64emu_t *emu, uintptr_t fcn) { iEEpu_t fn = (iEEpu_t)fcn; errno = emu->libc_err; R_EAX = fn(emu, from_ptriv(R_ESP + 4), from_ptri(uint32_t, R_ESP + 8)); emu->libc_err = errno; }\n",
            1, "iEEpi trampoline", "wrapper32.c")
        with open(p_w32c, "w", encoding="utf-8") as f:
            f.write(w32c)
        print("patch_pathmap2: 已应用 -> wrapper32.c(iEEpu trampoline)")
    if "iEEpu_32" not in w32h:
        w32h = apply(w32h,
            "void iEEpi_32(x64emu_t *emu, uintptr_t fnc);\n",
            "void iEEpi_32(x64emu_t *emu, uintptr_t fnc);\n"
            "void iEEpu_32(x64emu_t *emu, uintptr_t fnc);\n",
            1, "iEEpi 声明", "wrapper32.h")
        with open(p_w32h, "w", encoding="utf-8") as f:
            f.write(w32h)
        print("patch_pathmap2: 已应用 -> wrapper32.h(iEEpu 声明)")

    with open(p_libc32, "w", encoding="utf-8") as f:
        f.write(libc32)
    with open(p_priv32, "w", encoding="utf-8") as f:
        f.write(priv32)
    print("patch_pathmap2: 已应用 -> wrapped32/wrappedlibc.c(6处), wrapped32/private.h(3处)")


def patch(srcdir: str) -> int:
    _patch64(srcdir)
    _patch32(srcdir)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <box64源码目录>")
        sys.exit(2)
    sys.exit(patch(sys.argv[1]))
