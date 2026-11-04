// /WX を付けた TU でも、cl に無い clang の警告で解析失敗にしない(コードベース B で 13 TU が落ちた)
struct Base {
  virtual void F();
  virtual void G();
};
struct Derived : Base {
  void F() override;
  void G();  // -Winconsistent-missing-override
};
int Value();
Base *Global = nullptr;  // 列 1 の参照。ソース行に前の行を連結しない
