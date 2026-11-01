// 共有画像（static/images/share.png、1200×630）を、card.html から作り直す。
// 使い方（Playwright が入っている環境で）: node scripts/share_card/render.js
const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  await page.goto("file://" + path.resolve(__dirname, "card.html"), { waitUntil: "networkidle" });
  await page.waitForTimeout(1200); // Web フォントの読み込みを待つ
  await page.screenshot({ path: path.resolve(__dirname, "../../static/images/share.png") });
  await browser.close();
})();
