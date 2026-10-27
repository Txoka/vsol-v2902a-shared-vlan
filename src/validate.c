/* Functional constraints for the vendor's opaque, little-endian rule ABI.
 * This module validates records; it does not reproduce the vendor transport.
 */
struct field_limit {
    unsigned offset;
    unsigned width;
    unsigned maximum;
};

static const struct field_limit limits[] = {
    {0, 4, 2},     {4, 1, 8},     {5, 1, 2},  {6, 2, 4096},  {8, 4, 2},   {12, 1, 8},
    {13, 1, 2},    {14, 2, 4096}, {32, 1, 2}, {40, 4, 3},    {44, 1, 10}, {45, 1, 3},
    {46, 2, 4097}, {48, 4, 3},    {53, 1, 3}, {54, 2, 4097}, {56, 4, 2},
};

int sp_validate_rule(const void *record)
{
    const unsigned char *bytes = record;
    unsigned field, byte, value, tags;
    if (!bytes)
        return -1;
    for (field = 0; field < sizeof(limits) / sizeof(limits[0]); ++field) {
        value = 0;
        for (byte = 0; byte < limits[field].width; ++byte)
            value |= (unsigned)bytes[limits[field].offset + byte] << (byte * 8);
        if (value > limits[field].maximum)
            return -1;
    }
    tags = bytes[32];
    switch (bytes[36]) {
    case 0:
    case 1:
    case 21:
    case 22:
        return 0;
    case 23:
    case 31:
    case 40:
        return tags >= 1 ? 0 : -1;
    case 32:
    case 33:
    case 41:
    case 42:
        return tags == 2 ? 0 : -1;
    default:
        return -1;
    }
}
