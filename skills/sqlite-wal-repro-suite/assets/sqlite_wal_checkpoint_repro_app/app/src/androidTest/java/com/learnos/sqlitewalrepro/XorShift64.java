package com.learnos.sqlitewalrepro;

public final class XorShift64 {
    private long s;

    public XorShift64(long seed) {
        if (seed == 0) {
            seed = 0x9E3779B97F4A7C15L;
        }
        this.s = seed;
    }

    public long nextLong() {
        long x = s;
        x ^= (x << 13);
        x ^= (x >>> 7);
        x ^= (x << 17);
        s = x;
        return x;
    }
}

