/* Direct read-only query handles the empty-table encoding that stock
 * pon_get_vlan_rule reports as -1. Retains the kernel's opaque cursor. */
extern int open(const char *, int, ...);
extern int close(int);
extern int ioctl(int, unsigned long, ...);
int sp_read_rules(unsigned port, unsigned char rules[32][64])
{
    unsigned char q[208] = {0};
    unsigned i, j, n = 0;
    int fd, rc = -1;
    for (i = 0; i < 4; i++)
        q[i] = (unsigned char)(port >> (8 * i));
    fd = open("/dev/ponvlan", 0);
    if (fd < 0)
        return -1;
    for (i = 0; i < 32; i++) {
        if (ioctl(fd, 0x80006a04UL, q) < 0)
            goto done;
        if (!q[10]) {
            rc = n ? -1 : 0;
            goto done;
        }
        for (j = 0; j < 64; j++)
            rules[n][j] = q[80 + j];
        n++;
        if (q[10] == 255) {
            rc = (int)n;
            goto done;
        }
        if (q[10] != n)
            goto done;
    }
    rc = -1; /* Full tables must still end with the kernel's final-record marker. */
done:
    close(fd);
    return rc;
}
