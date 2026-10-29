// PWA の裏方(Service Worker。D-496)。ホーム画面に置けるようにするためだけのもの。
// このページ自身のファイルの読み込みを、そのままネットに取り次ぐだけで、何も保存しない(キャッシュしない。D-084、D-407)。
// ほかの場所(Pyodide の配布元)への読み込みには手を出さない。

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;
  event.respondWith(fetch(request));
});
