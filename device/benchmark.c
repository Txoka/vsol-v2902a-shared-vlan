/* Real driver benchmark ONLY on unused logical slots 17/18. Never global
 * reset, never ports 11/12. Abort unless both slots are initially empty and
 * their scalar settings match. Restore empty state before exit. */
struct timespec {
    long sec, nsec;
};
extern int clock_gettime(int, struct timespec *);
extern int printf(const char *, ...);
extern int fflush(void *);
extern int sched_setaffinity(int, unsigned long, const void *);
extern int sp_read_rules(unsigned, unsigned char[32][64]);
extern int sp_pon_add_vlan_rule(unsigned, const void *);
extern int sp_pon_del_vlan_rule(unsigned, unsigned);
extern int pon_add_vlan_rule(unsigned, const void *);
extern int pon_del_vlan_rule(unsigned, unsigned);
extern int pon_clean_vlan_rule(unsigned);
extern int pon_get_vlan_input_tpid(unsigned);
extern int pon_get_vlan_output_tpid(unsigned);
extern int pon_get_vlan_downstream_mode(unsigned);
extern int pon_get_default_vlan_status(unsigned);
extern int pon_get_vlan_dscp_map(unsigned, void *);
static unsigned char rule[64];
static unsigned long now(void)
{
    struct timespec t;
    clock_gettime(4, &t);
    return (unsigned long)t.sec * 1000000000UL + t.nsec;
}
static double cycle(unsigned shared, unsigned n)
{
    unsigned i;
    int rc;
    unsigned long a = now();
    for (i = 0; i < n; i++) {
        if (shared) {
            rc = sp_pon_add_vlan_rule(17, rule);
            if (!rc)
                rc = sp_pon_del_vlan_rule(17, 0);
        } else {
            rc = pon_add_vlan_rule(17, rule);
            if (!rc)
                rc = pon_add_vlan_rule(18, rule);
            if (!rc)
                rc = pon_del_vlan_rule(17, 0);
            if (!rc)
                rc = pon_del_vlan_rule(18, 0);
        }
        if (rc)
            return -1;
    }
    return (double)(now() - a) / n;
}
static void sort(double *a, unsigned n)
{
    unsigned i, j;
    for (i = 1; i < n; i++) {
        double x = a[i];
        j = i;
        while (j && a[j - 1] > x) {
            a[j] = a[j - 1];
            j--;
        }
        a[j] = x;
    }
}
static int run(void)
{
    unsigned char rows[32][64], a[24], b[24];
    double old[21], new[21];
    unsigned i;
    unsigned long mask = 1;
    if (sched_setaffinity(0, sizeof(mask), &mask))
        return 1;
    if (sp_read_rules(17, rows) != 0 || sp_read_rules(18, rows) != 0) {
        printf("BENCH_REFUSED_NONEMPTY\n");
        return 1;
    }
    if (pon_get_vlan_input_tpid(17) != pon_get_vlan_input_tpid(18) ||
        pon_get_vlan_output_tpid(17) != pon_get_vlan_output_tpid(18) ||
        pon_get_vlan_downstream_mode(17) != pon_get_vlan_downstream_mode(18) ||
        pon_get_default_vlan_status(17) != pon_get_default_vlan_status(18) ||
        pon_get_vlan_dscp_map(17, a) || pon_get_vlan_dscp_map(18, b))
        return 1;
    for (i = 0; i < 24; i++)
        if (a[i] != b[i])
            return 1;
    rule[4] = 8;
    rule[5] = 2;
    rule[7] = 16;
    rule[12] = 8;
    rule[13] = 2;
    rule[14] = 20;
    rule[32] = 1;
    rule[40] = 2;
    rule[45] = 2;
    rule[47] = 16;
    rule[48] = 2;
    rule[53] = 2;
    rule[55] = 16;
    rule[60] = 1;
    if (cycle(0, 50) < 0 || cycle(1, 50) < 0)
        goto failed;
    for (i = 0; i < 21; i++) {
        if (i & 1) {
            old[i] = cycle(0, 500);
            new[i] = cycle(1, 500);
        } else {
            new[i] = cycle(1, 500);
            old[i] = cycle(0, 500);
        }
        if (old[i] < 0 || new[i] < 0)
            goto failed;
    }
    if (sp_read_rules(17, rows) != 0 || sp_read_rules(18, rows) != 0)
        goto failed;
    sort(old, 21);
    sort(new, 21);
    printf("{\"device_io\":\"real-driver-unused-logical-slots\",\"ports\":[17,18],\"cpu\":0,"
           "\"samples\":21,\"cycles_per_sample\":500,\"operations_per_cycle\":\"add+delete on both "
           "ports\",\"stock_median_ns\":%.2f,\"shared_median_ns\":%.2f,\"ratio\":%.4f,\"stock_p95_"
           "ns\":%.2f,\"shared_p95_ns\":%.2f,\"both_slots_empty_after\":true}\n",
           old[10], new[10], new[10] / old[10], old[19], new[19]);
    return 0;
failed:
    pon_clean_vlan_rule(17);
    pon_clean_vlan_rule(18);
    printf("BENCH_FAILED_CLEANED_TEST_SLOTS\n");
    return 1;
}
__attribute__((noreturn)) void _start(void)
{
    int rc = run();
    fflush(0);
    register long x8 __asm__("x8") = 93;
    register long x0 __asm__("x0") = rc;
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x8) : "memory");
    __builtin_unreachable();
}
