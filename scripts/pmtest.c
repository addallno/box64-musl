// pathmask 覆盖自测：逐 syscall 路径操作，root 目录取 $PMROOT（默认 /tmp）
#define _GNU_SOURCE
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <utime.h>
#include <sys/time.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

static char ROOT[512];
static int fails = 0;

#define JOIN(a, b) ROOT "/" a "/" b
static char P[600], P2[600], D1[600], D2[600], LNK[600], HARD[600], SLA[600];

#define CK(name, expr) do { \
    errno = 0; \
    int _r = (expr); \
    if (_r != 0) { \
        printf("%-14s FAIL errno=%d\n", name, errno); \
        fails++; \
    } else { \
        printf("%-14s ok\n", name); \
    } \
} while (0)

int main(void) {
    const char *r = getenv("PMROOT");
    if (!r) r = "/tmp";
    snprintf(ROOT, sizeof ROOT, "%s", r);
    snprintf(P, sizeof P, "%s/pmt_src/agent.txt", ROOT);
    snprintf(P2, sizeof P2, "%s/pmt_dst/agent2.txt", ROOT);
    snprintf(D1, sizeof D1, "%s/pmt_src", ROOT);
    snprintf(D2, sizeof D2, "%s/pmt_dst", ROOT);
    snprintf(LNK, sizeof LNK, "%s/pmt_dst/link", ROOT);
    snprintf(HARD, sizeof HARD, "%s/pmt_dst/hard", ROOT);
    snprintf(SLA, sizeof SLA, "%s/pmt_dst/sla", ROOT);

    char s1[600], s2[600];
    snprintf(s1, sizeof s1, "%s/pmt_src", ROOT);
    snprintf(s2, sizeof s2, "%s/pmt_dst", ROOT);
    mkdir(s1, 0755);
    mkdir(s2, 0755);
    int fd = open(P, O_CREAT | O_RDWR | O_TRUNC, 0644);
    if (fd < 0) { perror("seed"); return 99; }
    if (write(fd, "hello-pm\n", 9) != 9) { perror("seedw"); return 99; }
    close(fd);

    char buf[256];
    struct stat st;
    { int f = open(P, O_RDONLY); if (f < 0) { printf("open FAIL errno=%d\n", errno); fails++; } else { printf("open ok\n"); close(f); } }
    CK("stat", stat(P, &st));
    CK("lstat", lstat(P, &st));
    CK("access", access(P, F_OK));
    CK("truncate", truncate(P, 5));
    CK("chmod", chmod(P, 0644));
    CK("chown", chown(P, -1, -1));
    CK("lchown", lchown(P, -1, -1));
    CK("utime", utime(P, NULL));
    CK("utimes", utimes(P, NULL));
    { char d[600]; snprintf(d, sizeof d, "%s/d1", D2); CK("mkdir", mkdir(d, 0755)); }
    CK("symlink", symlink(P, LNK));
    CK("readlink", readlink(LNK, buf, sizeof buf) > 0 ? 0 : -1);
    CK("rename", rename(P, P2));
    CK("link", link(P2, HARD));
    CK("stat64", stat(P2, &st));
    CK("setxattr", setxattr(P2, "user.t", "v", 1, 0));
    CK("getxattr", getxattr(P2, "user.t", buf, sizeof buf) == 1 ? 0 : -1);
    CK("listxattr", listxattr(P2, buf, sizeof buf) > 0 ? 0 : -1);
    CK("removexattr", removexattr(P2, "user.t"));
    CK("unlink", unlink(HARD));
    CK("unlinkat", unlinkat(AT_FDCWD, LNK, AT_SYMLINK_NOFOLLOW));
    { char d[600]; snprintf(d, sizeof d, "%s/d2", D2); CK("mkdirat", mkdirat(AT_FDCWD, d, 0755)); }
    CK("renameat", renameat(AT_FDCWD, P2, AT_FDCWD, P));
    CK("renameat2", renameat2(AT_FDCWD, P, AT_FDCWD, P2, 0));
    CK("symlinkat", symlinkat(P2, AT_FDCWD, SLA));
    CK("readlinkat", readlinkat(AT_FDCWD, SLA, buf, sizeof buf) > 0 ? 0 : -1);
    CK("fchmodat", fchmodat(AT_FDCWD, P2, 0644, 0));
    CK("faccessat", faccessat(AT_FDCWD, P2, F_OK, 0));
    CK("fstatat", fstatat(AT_FDCWD, P2, &st, 0));
    CK("utimensat", utimensat(AT_FDCWD, P2, NULL, 0));
    { int f = openat(AT_FDCWD, P2, O_RDONLY); if (f < 0) { printf("openat FAIL errno=%d\n", errno); fails++; } else { printf("openat ok\n"); close(f); } }
    {
        char *av[] = { P2, "noop", NULL };
        pid_t pid = fork();
        if (pid == 0) { execv(P2, av); _exit(127); }
        int stt; waitpid(pid, &stt, 0);
        CK("execv-127", (WIFEXITED(stt) && WEXITSTATUS(stt) == 127) ? 0 : -1);
    }
    { char d[600]; snprintf(d, sizeof d, "%s/d1", D2); CK("rmdir-d1", rmdir(d)); }
    { char d[600]; snprintf(d, sizeof d, "%s/d2", D2); CK("rmdir-d2", rmdir(d)); }
    printf("== fails=%d ==\n", fails);
    return fails ? 1 : 0;
}
