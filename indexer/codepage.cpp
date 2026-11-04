// Windows のコードページから UTF-8 への変換。windows.h のマクロが clang のヘッダーとぶつからないよう、別の翻訳単位にする。
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <string>

// Cp のバイト列を UTF-8 にする。Cp として正しくないバイトがあれば false。
bool codepageToUtf8(unsigned Cp, const char *P, size_t N, std::string &Out) {
  if (N == 0) { Out.clear(); return true; }
  if (N > 0x7fffffff) return false;
  int W = MultiByteToWideChar(Cp, MB_ERR_INVALID_CHARS, P, (int)N, nullptr, 0);
  if (W <= 0) return false;
  std::wstring Wide(W, L'\0');
  MultiByteToWideChar(Cp, MB_ERR_INVALID_CHARS, P, (int)N, Wide.data(), W);
  int U = WideCharToMultiByte(CP_UTF8, 0, Wide.data(), W, nullptr, 0, nullptr, nullptr);
  if (U <= 0) return false;
  Out.assign(U, '\0');
  WideCharToMultiByte(CP_UTF8, 0, Wide.data(), W, Out.data(), U, nullptr, nullptr);
  return true;
}

unsigned ansiCodepage() { return GetACP(); }
