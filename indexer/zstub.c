/* 公式配布の LLVMSupport.lib は zlib / zstd を参照するが、配布物には含まれない。
 * ltidx は圧縮を使わないので、crc32 だけ zlib と同じ結果を返す実装にし、残りは失敗を返す。 */
#include <stddef.h>

unsigned long crc32(unsigned long crc, const unsigned char *buf, unsigned int len) {
  static unsigned long table[256];
  static int ready;
  if (!ready) {
    for (unsigned long i = 0; i < 256; ++i) {
      unsigned long c = i;
      for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xEDB88320UL ^ (c >> 1) : c >> 1;
      table[i] = c;
    }
    ready = 1;
  }
  if (!buf) return 0;
  crc = ~crc & 0xFFFFFFFFUL;
  while (len--) crc = table[(crc ^ *buf++) & 0xFF] ^ (crc >> 8);
  return ~crc & 0xFFFFFFFFUL;
}

#define Z_MEM_ERROR (-4)
#define Z_DATA_ERROR (-3)
unsigned long compressBound(unsigned long n) { return n + (n >> 12) + (n >> 14) + (n >> 25) + 13; }
int compress2(unsigned char *d, unsigned long *dl, const unsigned char *s, unsigned long sl, int lv) { (void)d; (void)dl; (void)s; (void)sl; (void)lv; return Z_MEM_ERROR; }
int uncompress(unsigned char *d, unsigned long *dl, const unsigned char *s, unsigned long sl) { (void)d; (void)dl; (void)s; (void)sl; return Z_DATA_ERROR; }

#define ZSTD_FAIL ((size_t)-1)
void *ZSTD_createCCtx(void) { return NULL; }
size_t ZSTD_freeCCtx(void *c) { (void)c; return 0; }
size_t ZSTD_CCtx_setParameter(void *c, int p, int v) { (void)c; (void)p; (void)v; return ZSTD_FAIL; }
unsigned ZSTD_isError(size_t r) { return r > (size_t)-120; }
size_t ZSTD_compressBound(size_t n) { return n + (n >> 8) + 64; }
size_t ZSTD_compress2(void *c, void *d, size_t dc, const void *s, size_t ss) { (void)c; (void)d; (void)dc; (void)s; (void)ss; return ZSTD_FAIL; }
size_t ZSTD_decompress(void *d, size_t dc, const void *s, size_t ss) { (void)d; (void)dc; (void)s; (void)ss; return ZSTD_FAIL; }
const char *ZSTD_getErrorName(size_t r) { (void)r; return "zstd is not linked into ltidx"; }
