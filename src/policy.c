/* Experimental native adaptor extension. LAN1 (decimal 11) owns the shared
 * customer VLAN policy; LAN2 (decimal 12) follows it. OMCI identities/GEM maps
 * are untouched. This file is not installed on the ONT.
 * Device writes are sequential, NOT atomic; rollback can itself fail.
 * Only calls routed through the patched adaptor share this process lock. */
#ifndef SOURCE
#define SOURCE 11u
#endif
#ifndef FOLLOWER
#define FOLLOWER 12u
#endif
struct state {
    unsigned count;
    unsigned char rules[32][64];
    unsigned input_tpid, output_tpid, downstream, default_rule;
    unsigned char dscp[24];
};
extern int pon_add_vlan_rule(unsigned, const void *);
extern int pon_del_vlan_rule(unsigned, unsigned);
extern int pon_clean_vlan_rule(unsigned);
extern int pon_set_vlan_tpid(unsigned, unsigned, unsigned);
extern int pon_set_vlan_dscp_map(unsigned, const void *);
extern int pon_set_vlan_downstream_mode(unsigned, unsigned);
extern int pon_enable_default_vlan_rule(unsigned);
extern int pon_disable_default_vlan_rule(unsigned);
extern int pon_get_vlan_input_tpid(unsigned);
extern int pon_get_vlan_output_tpid(unsigned);
extern int pon_get_vlan_dscp_map(unsigned, void *);
extern int pon_get_vlan_downstream_mode(unsigned);
extern int pon_get_default_vlan_status(unsigned);
extern int sp_read_rules(unsigned, unsigned char[32][64]);
extern int sched_yield(void);
extern int sp_validate_rule(const void *);
extern long write(int, const void *, unsigned long);
static unsigned held;
unsigned sp_failed_recoveries;
static void lock(void)
{
    while (__atomic_exchange_n(&held, 1, __ATOMIC_ACQUIRE))
        sched_yield();
}
static void unlock(void) { __atomic_store_n(&held, 0, __ATOMIC_RELEASE); }
static int equal(const unsigned char *a, const unsigned char *b, unsigned n)
{
    unsigned i;
    for (i = 0; i < n; i++)
        if (a[i] != b[i])
            return 0;
    return 1;
}
static int rules_equal(const struct state *a, const struct state *b)
{
    unsigned i;
    if (a->count != b->count)
        return 0;
    for (i = 0; i < a->count; i++) {
        const unsigned char *x = a->rules[i], *y = b->rules[i];
        /* Compare upstream matching/treatment; omit padding and driver-generated
         * downstream fields 0x10..0x1f and derived complex flag 0x3c.
         * Layout confirmed against vendor assembly. */
        if (!equal(x, y, 16) || x[32] != y[32] || !equal(x + 34, y + 34, 3) ||
            !equal(x + 40, y + 40, 20))
            return 0;
    }
    return 1;
}
static int settings_equal(const struct state *a, const struct state *b)
{
    return a->input_tpid == b->input_tpid && a->output_tpid == b->output_tpid &&
           a->downstream == b->downstream && a->default_rule == b->default_rule &&
           equal(a->dscp, b->dscp, 24);
}
static int read_state(unsigned port, struct state *s)
{
    int n = sp_read_rules(port, s->rules), v;
    if (n < 0 || n > 32)
        return -1;
    s->count = (unsigned)n;
    v = pon_get_vlan_input_tpid(port);
    if (v < 0)
        return -1;
    s->input_tpid = (unsigned)v;
    v = pon_get_vlan_output_tpid(port);
    if (v < 0)
        return -1;
    s->output_tpid = (unsigned)v;
    v = pon_get_vlan_downstream_mode(port);
    if (v < 0)
        return -1;
    s->downstream = (unsigned)v;
    v = pon_get_default_vlan_status(port);
    if (v < 0)
        return -1;
    s->default_rule = (unsigned)v;
    return pon_get_vlan_dscp_map(port, s->dscp);
}
static int apply_state_known(unsigned port, const struct state *s, const struct state *known)
{
    struct state checked;
    const struct state *current = known;
    unsigned i;
    if (!current) {
        if (read_state(port, &checked))
            return -1;
        current = &checked;
    }
    if (settings_equal(s, current) && rules_equal(s, current))
        return 0;
    if (!settings_equal(s, current)) {
        if (pon_set_vlan_tpid(port, s->input_tpid, s->output_tpid) ||
            pon_set_vlan_dscp_map(port, s->dscp) ||
            pon_set_vlan_downstream_mode(port, s->downstream) ||
            (s->default_rule ? pon_enable_default_vlan_rule(port)
                             : pon_disable_default_vlan_rule(port)))
            return -1;
    }
    if (!rules_equal(s, current)) {
        if (pon_clean_vlan_rule(port))
            return -1;
        for (i = 0; i < s->count; i++)
            if (pon_add_vlan_rule(port, s->rules[i]))
                return -1;
    }
    if (read_state(port, &checked) || !rules_equal(s, &checked) || !settings_equal(s, &checked))
        return -1;
    return 0;
}
enum operation { ADD, DELETE, CLEAR, TPID, DSCP, DOWNSTREAM, DEFAULT };
static int stock(enum operation op, unsigned port, unsigned a, unsigned b, const void *data)
{
    switch (op) {
    case ADD:
        return pon_add_vlan_rule(port, data);
    case DELETE:
        return pon_del_vlan_rule(port, a);
    case CLEAR:
        return pon_clean_vlan_rule(port);
    case TPID:
        return pon_set_vlan_tpid(port, a, b);
    case DSCP:
        return pon_set_vlan_dscp_map(port, data);
    case DOWNSTREAM:
        return pon_set_vlan_downstream_mode(port, a);
    case DEFAULT:
        return a ? pon_enable_default_vlan_rule(port) : pon_disable_default_vlan_rule(port);
    }
    return -1;
}
/* Canonical shared state is owned by this adaptor. All normal updates are
 * incremental; direct out-of-process writers must not edit these two ports.
 * State 2 retains the pre-failure policy until recovery succeeds. */
static struct state policy;
static unsigned ready;
static void failed_recovery(void)
{
    static const char message[] =
        "shared-vlan: rollback FAILED; hardware policy needs inspection\n";
    ++sp_failed_recoveries;
    write(2, message, sizeof(message) - 1);
    ready = 2;
}
static int ensure_ready(void)
{
    struct state follower;
    if (ready == 1)
        return 0;
    if (ready == 2) {
        int a = apply_state_known(SOURCE, &policy, 0), b = apply_state_known(FOLLOWER, &policy, 0);
        if (a || b) {
            failed_recovery();
            return -1;
        }
        ready = 1;
        return 0;
    }
    if (read_state(SOURCE, &policy) || read_state(FOLLOWER, &follower))
        return -1;
    if (apply_state_known(FOLLOWER, &policy, &follower)) {
        if (apply_state_known(FOLLOWER, &follower, 0)) {
            /* The source policy is still the authoritative recovery target. */
            failed_recovery();
        }
        return -1;
    }
    ready = 1;
    return 0;
}
static void remember(enum operation op, unsigned a, unsigned b, const void *data)
{
    unsigned i, j;
    const unsigned char *bytes = data;
    switch (op) {
    case ADD:
        for (i = 0; i < 64; i++)
            policy.rules[policy.count][i] = bytes[i];
        policy.count++;
        break;
    case DELETE:
        for (i = a; i + 1 < policy.count; i++)
            for (j = 0; j < 64; j++)
                policy.rules[i][j] = policy.rules[i + 1][j];
        policy.count--;
        break;
    case CLEAR:
        policy.count = 0;
        break;
    case TPID:
        policy.input_tpid = a & 65535;
        policy.output_tpid = b & 65535;
        break;
    case DSCP:
        for (i = 0; i < 24; i++)
            policy.dscp[i] = bytes[i];
        break;
    case DOWNSTREAM:
        policy.downstream = a;
        break;
    case DEFAULT:
        policy.default_rule = a ? 1 : 0;
        break;
    }
}
static int dispatch(enum operation op, unsigned port, unsigned a, unsigned b, const void *data)
{
    int rc;
    if (port != SOURCE && port != FOLLOWER)
        return stock(op, port, a, b, data);
    if (op == DOWNSTREAM && a > 9)
        return -1;
    if (op == ADD && sp_validate_rule(data))
        return -1;
    lock();
    if (ensure_ready()) {
        unlock();
        return -1;
    }
    if ((op == DELETE && a >= policy.count) || (op == ADD && policy.count == 32)) {
        unlock();
        return -1;
    }
    if (port == FOLLOWER) {
        unlock();
        return 0;
    }
    rc = stock(op, SOURCE, a, b, data);
    if (!rc)
        rc = stock(op, FOLLOWER, a, b, data);
    if (!rc)
        remember(op, a, b, data);
    else {
        int x = apply_state_known(SOURCE, &policy, 0), y = apply_state_known(FOLLOWER, &policy, 0);
        if (x || y)
            failed_recovery();
    }
    unlock();
    return rc ? -1 : 0;
}
int sp_pon_add_vlan_rule(unsigned p, const void *r) { return dispatch(ADD, p, 0, 0, r); }
int sp_pon_del_vlan_rule(unsigned p, unsigned i) { return dispatch(DELETE, p, i, 0, 0); }
int sp_pon_clean_vlan_rule(unsigned p) { return dispatch(CLEAR, p, 0, 0, 0); }
int sp_pon_set_vlan_tpid(unsigned p, unsigned i, unsigned o) { return dispatch(TPID, p, i, o, 0); }
int sp_pon_set_vlan_dscp_map(unsigned p, const void *m) { return dispatch(DSCP, p, 0, 0, m); }
int sp_pon_set_vlan_downstream_mode(unsigned p, unsigned m)
{
    return dispatch(DOWNSTREAM, p, m, 0, 0);
}
int sp_pon_enable_default_vlan_rule(unsigned p) { return dispatch(DEFAULT, p, 1, 0, 0); }
/* Global MIB reset still affects all vendor ports, but is serialized with
 * this adaptor's paired updates. No attempt to roll back unrelated ports. */
extern int pon_clean_all_vlan_rule(void);
int sp_pon_clean_all_vlan_rule(void)
{
    int rc;
    lock();
    rc = pon_clean_all_vlan_rule();
    if (!rc) {
        policy.count = 0;
    } else if (ready)
        ready = 2;
    unlock();
    return rc ? -1 : 0;
}

/* Equal-length import aliases keep the vendor adaptor string table in place.
 * Aliases share the exact function address and argument ABI; no extra call. */
extern __typeof__(sp_pon_add_vlan_rule) spn_add_vlan_rule
    __attribute__((alias("sp_pon_add_vlan_rule")));
extern __typeof__(sp_pon_del_vlan_rule) spn_del_vlan_rule
    __attribute__((alias("sp_pon_del_vlan_rule")));
extern __typeof__(sp_pon_clean_vlan_rule) spn_clean_vlan_rule
    __attribute__((alias("sp_pon_clean_vlan_rule")));
extern __typeof__(sp_pon_clean_all_vlan_rule) spn_clean_all_vlan_rule
    __attribute__((alias("sp_pon_clean_all_vlan_rule")));
extern __typeof__(sp_pon_set_vlan_tpid) spn_set_vlan_tpid
    __attribute__((alias("sp_pon_set_vlan_tpid")));
extern __typeof__(sp_pon_set_vlan_dscp_map) spn_set_vlan_dscp_map
    __attribute__((alias("sp_pon_set_vlan_dscp_map")));
extern __typeof__(sp_pon_set_vlan_downstream_mode) spn_set_vlan_downstream_mode
    __attribute__((alias("sp_pon_set_vlan_downstream_mode")));
extern __typeof__(sp_pon_enable_default_vlan_rule) spn_enable_default_vlan_rule
    __attribute__((alias("sp_pon_enable_default_vlan_rule")));
