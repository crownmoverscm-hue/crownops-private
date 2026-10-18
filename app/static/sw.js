// No private data or API responses are cached. Internet is required to save.
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
  if(e.request.mode==='navigate') e.respondWith(fetch(e.request).catch(()=>new Response(
    '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#161616"><title>CrownOps offline</title><style>*{box-sizing:border-box}body{margin:0;padding:24px;background:#f6f5f2;color:#202020;font:18px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}main{max-width:480px;margin:10vh auto;background:white;border:1px solid #deddd9;border-radius:16px;padding:28px}h1{font-size:30px;line-height:1.2}a{display:block;text-align:center;background:#c4510a;color:white;padding:13px 18px;border-radius:10px;text-decoration:none;font-weight:650}a:focus-visible{outline:3px solid #161616;outline-offset:3px}</style><main><strong>CrownOps</strong><h1>You are offline</h1><p>Reconnect before opening or saving CrownOps records. No changes have been saved from this screen.</p><p>If your connection dropped while saving, check the record after reconnecting before trying again.</p><a href="">Try again</a></main></html>',
    {headers:{'Content-Type':'text/html'}})));
});
