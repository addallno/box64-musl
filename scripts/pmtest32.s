# pmtest32.s — 静态 i386 raw syscall 路径测试（sysgap32 验证，无 libc）
# 输出 stdout 68 字节 = 17 × int32 小端，顺序：
#   [0]open(5) [1]truncate(92) [2]link(9) [3]chown(182) [4]lchown(16)
#   [5]truncate64(193) [6]setxattr(226) [7]getxattr(229) [8]removexattr(235)
#   [9]unlink(10) [10]mkdir(39) [11]rmdir(40) [12]symlink(83) [13]readlink(85)
#   [14]access(33) [15]chmod(15) [16]rename(38)
# -ENOSYS=-38 → 该项缺 case；0/正值=接通；其它=errno
# i386 int0x80 参数序: ebx,ecx,edx,esi,edi,ebp
# 组装: as --32 -o pmtest32.o pmtest32.s && ld -m elf_i386 -o pmtest32 pmtest32.o
.section .data
p1: .asciz "/tmp/p32a"
p2: .asciz "/tmp/p32b"
p3: .asciz "/tmp/p32c"
p4: .asciz "/tmp/p32d"
p5: .asciz "/tmp/p32l"
xn: .asciz "user.t"
xv: .ascii "v"
obuf: .space 80
rbuf: .space 40
gbuf: .space 16

.section .text
.globl _start
_start:
    movl $obuf, %edi

    # open(p1, O_CREAT|O_RDWR|O_TRUNC=0x242, 0644)
    movl $5, %eax
    movl $p1, %ebx
    movl $0x242, %ecx
    movl $0644, %edx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # truncate(p1, 3)
    movl $92, %eax
    movl $p1, %ebx
    movl $3, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # link(p1, p2)
    movl $9, %eax
    movl $p1, %ebx
    movl $p2, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # chown(p1, -1, -1)
    movl $182, %eax
    movl $p1, %ebx
    movl $-1, %ecx
    movl $-1, %edx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # lchown(p1, -1, -1)
    movl $16, %eax
    movl $p1, %ebx
    movl $-1, %ecx
    movl $-1, %edx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # truncate64(p1, lo=3, hi=0)
    movl $193, %eax
    movl $p1, %ebx
    movl $3, %ecx
    xorl %edx, %edx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # setxattr(p1, xn, xv, 1, flags=0) 第5参=EDI（保存写指针）
    pushl %edi
    movl $226, %eax
    movl $p1, %ebx
    movl $xn, %ecx
    movl $xv, %edx
    movl $1, %esi
    xorl %edi, %edi
    int $0x80
    popl %edi
    movl %eax, (%edi)
    addl $4, %edi

    # getxattr(p1, xn, gbuf, 16)
    movl $229, %eax
    movl $p1, %ebx
    movl $xn, %ecx
    movl $gbuf, %edx
    movl $16, %esi
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # removexattr(p1, xn)
    movl $235, %eax
    movl $p1, %ebx
    movl $xn, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # unlink(p2)；失败于 link 的 EACCES 时 p2 不存在 → -2
    movl $10, %eax
    movl $p2, %ebx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # mkdir(p4, 0755)
    movl $39, %eax
    movl $p4, %ebx
    movl $0755, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # rmdir(p4)
    movl $40, %eax
    movl $p4, %ebx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # unlink(p5) 清残留保幂等（结果不记录）
    movl $10, %eax
    movl $p5, %ebx
    int $0x80

    # symlink(p1, p5)
    movl $83, %eax
    movl $p1, %ebx
    movl $p5, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # readlink(p5, rbuf, 40)
    movl $85, %eax
    movl $p5, %ebx
    movl $rbuf, %ecx
    movl $40, %edx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # access(p1, F_OK)
    movl $33, %eax
    movl $p1, %ebx
    xorl %ecx, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # chmod(p1, 0644)
    movl $15, %eax
    movl $p1, %ebx
    movl $0644, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # rename(p1, p3) 最后
    movl $38, %eax
    movl $p1, %ebx
    movl $p3, %ecx
    int $0x80
    movl %eax, (%edi)
    addl $4, %edi

    # write(1, obuf, 68)
    movl $4, %eax
    movl $1, %ebx
    movl $obuf, %ecx
    movl $68, %edx
    int $0x80

    # exit(0)
    movl $1, %eax
    xorl %ebx, %ebx
    int $0x80
