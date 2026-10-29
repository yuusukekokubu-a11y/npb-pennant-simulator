// PWA のアイコン(web/icons/*.png)を、元の絵 web/icons/icon.svg から作り直す、開発者向けのスクリプト(D-496)。
// 絵を変えたら、これで作り直して同じ PR に入れる。Playwright(ブラウザを自動で操作する道具)の Chromium で描く。
//
// 使い方:
//   node scripts/make_icons.mjs
//   (playwright が見つからないときは、PLAYWRIGHT_MODULE に playwright の index.mjs の場所を入れる。通し確認と同じ)

import { readFileSync } from "node:fs";
import { join } from "node:path";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const ICONS = new URL("../web/icons/", import.meta.url).pathname;
const SIZES = { "icon-192.png": 192, "icon-512.png": 512, "apple-touch-icon.png": 180 };

const svg = readFileSync(join(ICONS, "icon.svg"), "utf8");
const browser = await chromium.launch();
for (const [name, size] of Object.entries(SIZES)) {
  const page = await browser.newPage({ viewport: { width: size, height: size } });
  await page.setContent(`<style>html,body{margin:0}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`);
  await page.screenshot({ path: join(ICONS, name), omitBackground: false });
  await page.close();
  console.log(`${name}(${size}×${size})`);
}
await browser.close();
