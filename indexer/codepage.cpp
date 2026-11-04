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

// 8.3 形式の短い名前。取れなければ(8.3 名を作らないボリュームなど)そのまま返す
std::string shortPath(const std::string &P) {
  int W = MultiByteToWideChar(CP_UTF8, 0, P.data(), (int)P.size(), nullptr, 0);
  if (W <= 0) return P;
  std::wstring Wide(W, L'\0');
  MultiByteToWideChar(CP_UTF8, 0, P.data(), (int)P.size(), Wide.data(), W);
  DWORD N = GetShortPathNameW(Wide.c_str(), nullptr, 0);
  if (N == 0) return P;
  std::wstring Short(N, L'\0');
  N = GetShortPathNameW(Wide.c_str(), Short.data(), N);
  if (N == 0) return P;
  Short.resize(N);
  int U = WideCharToMultiByte(CP_UTF8, 0, Short.data(), (int)N, nullptr, 0, nullptr, nullptr);
  if (U <= 0) return P;
  std::string Out(U, '\0');
  WideCharToMultiByte(CP_UTF8, 0, Short.data(), (int)N, Out.data(), U, nullptr, nullptr);
  return Out;
}
