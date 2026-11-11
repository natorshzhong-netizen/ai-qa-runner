/**
 * ShopMini —— ai-qa-runner 的离线被测应用。
 *
 * 默认行为全部正确。URL 上的 ?bugs= 用来注入缺陷，供归因评测使用：
 *   ?bugs=1            开启全部缺陷
 *   ?bugs=login        登录成功后不进入商城
 *   ?bugs=cart         购物车合计忽略数量
 *   ?bugs=validation   空用户名不给出必填提示
 *   ?bugs=flaky        加入购物车有 45% 概率静默失败（用于演示 flaky 检测）
 * 可以组合：?bugs=cart,flaky
 */

const BUGS = new Set(
  (() => {
    const raw = new URLSearchParams(location.search).get("bugs") || "";
    if (!raw) return [];
    if (raw === "1" || raw === "all") return ["login", "cart", "validation"];
    return raw
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
  })()
);

const PRODUCTS = [
  { id: "keyboard", name: "机械键盘", price: 299, thumb: "⌨️", keywords: "键盘 输入 办公" },
  { id: "mouse", name: "无线鼠标", price: 129, thumb: "🖱️", keywords: "鼠标 无线 办公" },
  { id: "monitor", name: "27 英寸显示器", price: 1099, thumb: "🖥️", keywords: "显示器 屏幕 27" },
  { id: "headset", name: "降噪耳机", price: 399, thumb: "🎧", keywords: "耳机 降噪 音乐" },
];

const state = {
  cart: new Map(),
  query: "",
};

const loginView = document.querySelector("#login-view");
const shopView = document.querySelector("#shop-view");
const loginForm = document.querySelector("#login-form");
const loginMessage = document.querySelector("#login-message");
const usernameInput = document.querySelector("#username");
const passwordInput = document.querySelector("#password");
const userName = document.querySelector("#user-name");
const productGrid = document.querySelector("#product-grid");
const searchInput = document.querySelector("#search");
const productCount = document.querySelector("[data-testid='product-count']");
const cartCount = document.querySelector("[data-testid='cart-count']");
const cartTotal = document.querySelector("[data-testid='cart-total']");

document.body.dataset.bugs = [...BUGS].join(",");

function money(value) {
  return `¥${value}`;
}

function setMessage(text, ok = false) {
  loginMessage.textContent = text;
  loginMessage.classList.toggle("ok", ok);
}

function cartQuantity() {
  return [...state.cart.values()].reduce((sum, qty) => sum + qty, 0);
}

function cartAmount() {
  let total = 0;
  for (const [id, qty] of state.cart) {
    const product = PRODUCTS.find((p) => p.id === id);
    if (!product) continue;
    // BUG(cart)：合计只算单价，忽略数量。
    total += BUGS.has("cart") ? product.price : product.price * qty;
  }
  return total;
}

function renderCart() {
  cartCount.textContent = String(cartQuantity());
  cartTotal.textContent = money(cartAmount());
}

function addToCart(id) {
  // BUG(flaky)：模拟偶发的静默失败，用来演示多轮运行检出 flaky。
  if (BUGS.has("flaky") && Math.random() < 0.45) return;
  state.cart.set(id, (state.cart.get(id) || 0) + 1);
  renderCart();
}

function visibleProducts() {
  const query = state.query.trim().toLowerCase();
  if (!query) return PRODUCTS;
  return PRODUCTS.filter(
    (p) => p.name.toLowerCase().includes(query) || p.keywords.includes(query)
  );
}

function renderProducts() {
  const items = visibleProducts();
  productGrid.replaceChildren();
  productCount.textContent = `共 ${items.length} 件商品`;

  for (const product of items) {
    const card = document.createElement("article");
    card.className = "product";
    card.dataset.name = product.name;

    const thumb = document.createElement("div");
    thumb.className = "thumb";
    thumb.textContent = product.thumb;

    const title = document.createElement("h3");
    title.textContent = product.name;

    const price = document.createElement("p");
    price.className = "price";
    price.textContent = money(product.price);

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "加入购物车";
    button.dataset.testid = `add-${product.id}`;
    button.addEventListener("click", () => addToCart(product.id));

    card.append(thumb, title, price, button);
    productGrid.append(card);
  }
}

function enterShop(user) {
  userName.textContent = user;
  loginView.hidden = true;
  shopView.hidden = false;
  renderProducts();
  renderCart();
}

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  setMessage("");

  const username = usernameInput.value.trim();
  const password = passwordInput.value;

  if (!username && !BUGS.has("validation")) {
    setMessage("请输入用户名");
    usernameInput.focus();
    return;
  }
  if (!password) {
    setMessage("请输入密码");
    passwordInput.focus();
    return;
  }
  if (username !== "alice" || password !== "secret123") {
    setMessage("用户名或密码错误");
    return;
  }

  // BUG(login)：登录成功但不切换视图。
  if (BUGS.has("login")) {
    setMessage("登录成功", true);
    return;
  }
  setMessage("登录成功", true);
  enterShop(username);
});

searchInput.addEventListener("input", () => {
  state.query = searchInput.value;
  renderProducts();
});

document.querySelector("#logout").addEventListener("click", () => {
  state.cart.clear();
  state.query = "";
  searchInput.value = "";
  usernameInput.value = "";
  passwordInput.value = "";
  setMessage("");
  loginView.hidden = false;
  shopView.hidden = true;
  renderCart();
});

renderCart();
