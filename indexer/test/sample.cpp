// ltidx の回帰テスト用。各行の期待は expected.tsv / expected.tsv.skip.tsv にある。
struct A { void Reset(); };

/* コメント内の呼び出しは索引に入らない
   a.Reset(); */
void Plain(A &a) {
  a.Reset();  // a.Reset();
#if 0
  a.Reset();  // 無効な #if の中
#endif
}

// テンプレート内の using 宣言越しの呼び出し(役割 call-using)
template <class T> struct Base { void Reset() {} };
template <class T> struct Mid : Base<T> {
  using Super = Base<T>;
  using Super::Reset;
};
template <class T> struct Leaf : Mid<T> {
  using Super = Mid<T>;
  void Used() { Super::Reset(); }    // 実体化される
  void Unused() { Super::Reset(); }  // 実体化されない
};

// 解決先が決まらない呼び出し(役割 dep)
template <class T> void Generic(T &t) { t.Reset(); }

void Use() {
  Leaf<int> l;
  l.Used();
}
