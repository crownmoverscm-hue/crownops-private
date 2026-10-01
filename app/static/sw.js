// No private data or API responses are cached. Internet is required to save.
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
  if(e.request.mode==='navigate') e.respondWith(fetch(e.request).catch(()=>new Response(
    '<!doctype html><title>CrownOps offline</title><h1>You are offline</h1><p>Reconnect before opening or saving CrownOps records. No changes have been saved.</p>',
    {headers:{'Content-Type':'text/html'}})));
});
