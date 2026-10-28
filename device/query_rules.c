/* Read-only ARM64 Linux probe. Only PON VLAN operation zero (read) is used.
 * Output: per port, u32 port, signed i32 count, then count 64-byte rules.
 * No libc, no network calls, no flash access. */
typedef unsigned long ulong;
static long call3(long n, long a, long b, long c)
{
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}
static int write_all(const void *data, ulong size)
{
    const char *p = data;
    while (size) {
        long n = call3(64, 1, (long)p, size);
        if (n <= 0)
            return -1;
        p += n;
        size -= n;
    }
    return 0;
}
static int query(unsigned port)
{
    unsigned char request[208] = {0};
    unsigned char rules[32][64];
    unsigned result[2] = {port, 0};
    long fd, rc;
    unsigned i, j, count = 0;
    request[0] = (unsigned char)port;
    fd = call3(56, -100, (long)"/dev/ponvlan", 0); /* openat(AT_FDCWD, ..., O_RDONLY) */
    if (fd < 0) {
        result[1] = (unsigned)-1;
        return write_all(result, 8);
    }
    for (i = 0; i < 32; i++) {
        rc = call3(29, fd, 0x80006a04, (long)request);
        if (rc < 0) {
            result[1] = (unsigned)-2;
            break;
        }
        if (request[10] == 0)
            break; /* Verified kernel encoding: no rows. */
        for (j = 0; j < 64; j++)
            rules[count][j] = request[80 + j];
        count++;
        if (request[10] == 255)
            break;
        if (request[10] != count) {
            result[1] = (unsigned)-3;
            break;
        }
    }
    call3(57, fd, 0, 0);
    if ((int)result[1] >= 0)
        result[1] = count;
    if (write_all(result, 8) < 0)
        return -1;
    return (int)result[1] < 0 ? 0 : write_all(rules, count * 64);
}
__attribute__((noreturn)) void _start(void)
{
    int rc = query(11);
    if (rc == 0)
        rc = query(12);
    call3(93, rc ? 1 : 0, 0, 0);
    __builtin_unreachable();
}
