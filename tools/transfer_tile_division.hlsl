// Experimental bounded tile-address division, not a general uint division.
// Preconditions: n <= 2047, 1 <= d <= 1023. No production shader uses this yet.
uint2 AotDivideTile(uint n, uint d) {
  precise float quotient = float(n) / float(d);
  quotient = quotient + 0.00048828125f; // 2^-11, below the smallest nonzero fraction.
  uint q = uint(quotient);
  return uint2(q, n - q * d);
}
