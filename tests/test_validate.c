#include <assert.h>
#include <stdio.h>
extern int sp_validate_rule(const void *);

int main(void)
{
    unsigned char rule[64] = {0};
    unsigned tags, method;
    assert(sp_validate_rule(0) == -1);
    for (tags = 0; tags < 4; ++tags) {
        rule[32] = tags;
        for (method = 0; method < 256; ++method) {
            int allowed = method == 0 || method == 1 || method == 21 || method == 22;
            allowed |= tags >= 1 && (method == 23 || method == 31 || method == 40);
            allowed |= tags == 2 && (method == 32 || method == 33 || method == 41 || method == 42);
            rule[36] = method;
            assert((sp_validate_rule(rule) == 0) == (tags <= 2 && allowed));
        }
    }
    rule[32] = rule[36] = 0;
    rule[6] = 0;
    rule[7] = 16; /* VID wildcard 4096 is valid. */
    assert(!sp_validate_rule(rule));
    rule[6] = 1;
    assert(sp_validate_rule(rule) == -1);
    rule[6] = rule[7] = 0;
    rule[46] = 1;
    rule[47] = 16; /* Treatment sentinel 4097. */
    assert(!sp_validate_rule(rule));
    rule[46] = 2;
    assert(sp_validate_rule(rule) == -1);
    rule[46] = rule[47] = 0;
    rule[44] = 11;
    assert(sp_validate_rule(rule) == -1);
    rule[44] = 10;
    assert(!sp_validate_rule(rule));
    puts("PASS: rule methods, tag counts, wildcard/sentinel values and invalid bounds");
}
