// PiStock — service worker
// Strategie : cache uniquement les assets statiques lourds qui changent
// peu (model-viewer + icones). Le manifest.json et les pages HTML
// passent TOUJOURS par le reseau pour eviter de servir une version
// obsolete (cas tres frequent en developpement).

// IMPORTANT : ce nom de cache doit etre bumpe a chaque fois qu'on
// modifie la liste STATIC_ASSETS ou la strategie de cache.
// Le navigateur compare ce fichier byte-a-byte ; tant qu'il est
// identique, aucune mise a jour n'est declenchee.
const CACHE_NAME = 'pistock-v2';

const STATIC_ASSETS = [
    '/static/model-viewer.min.js',
    '/static/icon-192.png',
    '/static/icon-512.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(STATIC_ASSETS))
            .then(() => self.skipWaiting())
            .catch((err) => console.warn('SW install error:', err))
    );
});

self.addEventListener('activate', (event) => {
    // Purge des caches d'anciennes versions
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(
                names.filter((n) => n !== CACHE_NAME)
                     .map((n) => caches.delete(n))
            )
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    const isStaticAsset = STATIC_ASSETS.some((path) =>
        url.pathname === path
    );
    if (!isStaticAsset) {
        return; // pas d'interception : tout le reste passe au reseau
    }
    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) return cached;
            return fetch(event.request).then((response) => {
                if (response && response.status === 200) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME)
                          .then((cache) => cache.put(event.request, clone));
                }
                return response;
            });
        })
    );
});
