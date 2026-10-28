#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
struct device {
    unsigned n;
    unsigned char r[32][64];
    unsigned in, out, down, def;
    unsigned char dscp[24];
};
static struct device ports[64];
static unsigned calls, fail_add_once, fail_add_always, fail_global_once;
extern unsigned sp_failed_recoveries;
int sp_pon_add_vlan_rule(unsigned, const void *);
int sp_pon_del_vlan_rule(unsigned, unsigned);
int sp_pon_clean_vlan_rule(unsigned);
int sp_pon_set_vlan_tpid(unsigned, unsigned, unsigned);
int sp_pon_set_vlan_dscp_map(unsigned, const void *);
int sp_pon_set_vlan_downstream_mode(unsigned, unsigned);
int sp_pon_enable_default_vlan_rule(unsigned);
int sp_pon_clean_all_vlan_rule(void);
int sp_read_rules(unsigned p, unsigned char out[32][64])
{
    memcpy(out, ports[p].r, ports[p].n * 64);
    return ports[p].n;
}
int pon_add_vlan_rule(unsigned p, const void *r)
{
    ++calls;
    if (p == fail_add_always) {
        ports[p].n = 0;
        return -1;
    }
    if (p == fail_add_once) {
        fail_add_once = 0;
        return -1;
    }
    if (ports[p].n == 32)
        return -1;
    memcpy(ports[p].r[ports[p].n++], r, 64);
    return 0;
}
int pon_del_vlan_rule(unsigned p, unsigned i)
{
    ++calls;
    if (i >= ports[p].n)
        return -1;
    memmove(ports[p].r[i], ports[p].r[i + 1], (ports[p].n - i - 1) * 64);
    ports[p].n--;
    return 0;
}
int pon_clean_all_vlan_rule(void)
{
    unsigned p;
    for (p = 0; p < 64; p++)
        ports[p].n = 0;
    if (fail_global_once) {
        fail_global_once = 0;
        return -1;
    }
    return 0;
}
int pon_clean_vlan_rule(unsigned p)
{
    ++calls;
    ports[p].n = 0;
    return 0;
}
int pon_set_vlan_tpid(unsigned p, unsigned i, unsigned o)
{
    ++calls;
    ports[p].in = i;
    ports[p].out = o;
    return 0;
}
int pon_set_vlan_dscp_map(unsigned p, const void *m)
{
    ++calls;
    memcpy(ports[p].dscp, m, 24);
    return 0;
}
int pon_set_vlan_downstream_mode(unsigned p, unsigned m)
{
    ++calls;
    ports[p].down = m;
    return 0;
}
int pon_enable_default_vlan_rule(unsigned p)
{
    ++calls;
    ports[p].def = 1;
    return 0;
}
int pon_disable_default_vlan_rule(unsigned p)
{
    ++calls;
    ports[p].def = 0;
    return 0;
}
int pon_get_vlan_input_tpid(unsigned p) { return ports[p].in; }
int pon_get_vlan_output_tpid(unsigned p) { return ports[p].out; }
int pon_get_vlan_downstream_mode(unsigned p) { return ports[p].down; }
int pon_get_default_vlan_status(unsigned p) { return ports[p].def; }
int pon_get_vlan_dscp_map(unsigned p, void *m)
{
    memcpy(m, ports[p].dscp, 24);
    return 0;
}
static void same(void)
{
    assert(ports[11].n == ports[12].n);
    assert(!memcmp(ports[11].r, ports[12].r, ports[11].n * 64));
    assert(ports[11].in == ports[12].in && ports[11].out == ports[12].out);
    assert(ports[11].down == ports[12].down && ports[11].def == ports[12].def);
    assert(!memcmp(ports[11].dscp, ports[12].dscp, 24));
}
static void *worker(void *arg)
{
    unsigned i;
    unsigned char row[64] = {0};
    row[0] = (unsigned long)arg;
    for (i = 0; i < 8; i++) {
        row[15] = i;
        assert(!sp_pon_add_vlan_rule(11, row));
    }
    return 0;
}
int main(void)
{
    unsigned char row[64] = {0}, map[24] = {9};
    unsigned i;
    struct device old;
    pthread_t t[2];
    assert(!sp_pon_add_vlan_rule(11, row));
    same();
    assert(ports[11].n == 1);
    row[0] = 2;
    assert(!sp_pon_add_vlan_rule(12, row));
    same();
    assert(ports[11].n == 1);
    assert(!sp_pon_clean_vlan_rule(12));
    same();
    assert(ports[11].n == 1);
    assert(!sp_pon_set_vlan_tpid(11, 0x8100, 0x88a8));
    same();
    assert(ports[12].out == 0x88a8);
    assert(!sp_pon_set_vlan_tpid(12, 0xffff, 0xffff));
    same();
    assert(ports[11].in == 0x8100);
    assert(!sp_pon_set_vlan_dscp_map(11, map));
    same();
    assert(ports[12].dscp[0] == 9);
    assert(!sp_pon_set_vlan_downstream_mode(11, 3));
    same();
    assert(!sp_pon_enable_default_vlan_rule(11));
    same();
    assert(sp_pon_del_vlan_rule(12, 31) == -1);
    same();
    assert(sp_pon_set_vlan_downstream_mode(12, 10) == -1);
    same();
    {
        unsigned char invalid[64] = {0};
        invalid[32] = 3;
        assert(sp_pon_add_vlan_rule(12, invalid) == -1);
        same();
    }
    old = ports[11];
    fail_add_once = 12;
    assert(sp_pon_add_vlan_rule(11, row) == -1);
    same();
    assert(!memcmp(&old.r, &ports[11].r, old.n * 64) && ports[11].n == old.n);
    assert(sp_failed_recoveries == 0);
    assert(!sp_pon_add_vlan_rule(11, row));
    same();
    assert(!sp_pon_del_vlan_rule(11, 0));
    same();
    assert(ports[11].n == 1);
    assert(!sp_pon_del_vlan_rule(12, 0));
    same();
    assert(ports[11].n == 1);
    assert(!sp_pon_clean_vlan_rule(11));
    same();
    assert(!ports[11].n);
    for (i = 0; i < 32; i++) {
        row[14] = i;
        assert(!sp_pon_add_vlan_rule(11, row));
    }
    same();
    assert(sp_pon_add_vlan_rule(11, row) == -1);
    same();
    assert(ports[11].n == 32);
    assert(!sp_pon_add_vlan_rule(7, row));
    assert(!sp_pon_clean_all_vlan_rule());
    assert(ports[7].n == 0 && ports[11].n == 0);
    same();
    assert(!pthread_create(&t[0], 0, worker, (void *)1));
    assert(!pthread_create(&t[1], 0, worker, (void *)2));
    assert(!pthread_join(t[0], 0));
    assert(!pthread_join(t[1], 0));
    same();
    assert(ports[11].n == 16);
    assert(!sp_pon_add_vlan_rule(7, row));
    assert(ports[7].n == 1);
    same();
    fail_global_once = 1;
    assert(sp_pon_clean_all_vlan_rule() == -1);
    assert(ports[7].n == 0); /* Global rollback does not restore unrelated ports. */
    assert(!sp_pon_clean_vlan_rule(12)); /* Recovery restores the canonical pair. */
    same();
    assert(ports[11].n == 16);
    fail_add_always = 12;
    assert(sp_pon_add_vlan_rule(11, row) == -1);
    assert(sp_failed_recoveries == 1);
    assert(ports[11].n == 16 && ports[12].n == 0);
    fail_add_always = 0;
    /* A later follower event reconciles the failed port from the source again. */
    assert(!sp_pon_clean_vlan_rule(12));
    same();
    assert(calls > 0);
    puts("PASS: shared native policy, follower isolation, settings, deletion/reset, capacity, "
         "rollback, failed rollback, recovery, concurrent calls, unrelated port");
}
