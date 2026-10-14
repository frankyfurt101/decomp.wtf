/* Original test program, CC0-1.0. No Nintendo code, SDK or game assets. */
typedef unsigned int u32;
u32 clamp_value(u32 value, u32 lo, u32 hi);
u32 checksum(const u32 *values, u32 count);
volatile u32 output;
const u32 values[] = {4, 11, 23, 42};
void _start(void) {
    output = clamp_value(checksum(values, 4), 10, 100);
    for (;;) { __asm__ volatile("nop"); }
}
u32 clamp_value(u32 value, u32 lo, u32 hi) {
    if (value < lo) return lo;
    if (value > hi) return hi;
    return value;
}
u32 checksum(const u32 *input, u32 count) {
    u32 result = 0;
    for (u32 i = 0; i < count; i++) result = result * 33 + input[i];
    return result;
}
