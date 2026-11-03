/**
 * tests/verify_ui.mjs
 * UI の汎用検証ゲート。ページを開き、ブラウザのエラーが 0 件であることを Exit Code で判定する。
 *   - コンソールのエラー、未捕捉の例外、読み込みの失敗、HTTP 4xx/5xx を数える
 *   - スクリーンショットは人間が原因を探すための補助で、合否には使わない
 *
 * 画面の「正しさ」そのもの（レイアウトなど）は、プロジェクトごとに測れる条件を書き足す。
 * 組版の例は examples/compositor/tests/verify_layout.mjs を参照。
 *
 * 準備: npm install -D playwright && npx playwright install chromium
 * 実行: node tests/verify_ui.mjs <URL または HTMLのパス>
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const arg = process.argv[2] || 'http://localhost:3000';
const target = /^https?:\/\//.test(arg) ? arg : pathToFileURL(path.resolve(arg)).href;
// スクリーンショットは入力ごとに別の名前で残す（複数のページを続けて検査しても上書きしない）
const name = (/^https?:\/\//.test(arg) ? arg.replace(/^https?:\/\//, '') : path.basename(arg, path.extname(arg)))
  .replace(/[^A-Za-z0-9._-]+/g, '_').slice(0, 80) || 'page';
const OUT_DIR = path.resolve('test-results');
const SHOT = path.join(OUT_DIR, name + '.png');
// CI でネットワークに出られない場合、Web フォントの取得失敗はエラーに数えない
const IGNORE = [/fonts\.(googleapis|gstatic)\.com/];
const ignored = (url) => IGNORE.some((re) => re.test(url));

fs.mkdirSync(OUT_DIR, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

const errors = [];
page.on('console', (m) => {
  // 読み込み失敗はコンソールにも出るが、URL は location() にしか入っていない
  if (m.type() === 'error' && !ignored(m.location().url || '')) errors.push('console: ' + m.text());
});
page.on('pageerror', (e) => errors.push('uncaught: ' + e.message));
page.on('requestfailed', (r) => { if (!ignored(r.url())) errors.push('request failed: ' + r.url()); });
page.on('response', (r) => { if (r.status() >= 400 && !ignored(r.url())) errors.push('HTTP ' + r.status() + ': ' + r.url()); });

try {
  const res = await page.goto(target, { waitUntil: 'load', timeout: 15000 });
  if (res && res.status() >= 400) throw new Error('HTTP ' + res.status());
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: SHOT, fullPage: true });
} catch (e) {
  console.error('[verify FATAL] ' + e.message);
  await browser.close();
  process.exit(1);
}
await browser.close();

for (const e of errors) console.error('[browser] ' + e);
if (errors.length) {
  console.error(`[verify FAILED] browser errors: ${errors.length}（補助のスクリーンショット: ${SHOT}）`);
  process.exit(1);
}
console.log(`[verify PASSED] 0 browser errors（補助のスクリーンショット: ${SHOT}）`);
process.exit(0);
