// Service Worker для NeoChat v10
// Поддерживает фоновые уведомления и кэширование

const CACHE_NAME = 'neochat-v10-cache';
const FILES_TO_CACHE = [
  '/',
  '/index.html',
  '/service-worker.js'
];

// Установка Service Worker
self.addEventListener('install', (event) => {
  console.log('[SW] Installing Service Worker...');
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Caching files...');
      return cache.addAll(FILES_TO_CACHE).catch(() => {
        console.log('[SW] Some files could not be cached');
      });
    })
  );
  self.skipWaiting();
});

// Активация Service Worker
self.addEventListener('activate', (event) => {
  console.log('[SW] Service Worker activated');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('[SW] Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Перехват запросов (Network first, fallback to cache)
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Если ответ успешный, кэшируем его
        if (response.status === 200) {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return response;
      })
      .catch(() => {
        // Если нет сети, используем кэш
        return caches.match(event.request).then((response) => {
          return response || new Response('Offline - No cached response', {
            status: 503,
            statusText: 'Service Unavailable',
            headers: new Headers({ 'Content-Type': 'text/plain' })
          });
        });
      })
  );
});

// Обработка push-уведомлений
self.addEventListener('push', (event) => {
  console.log('[SW] Push notification received:', event);
  
  const options = {
    body: event.data ? event.data.text() : 'Новое сообщение в NeoChat',
    icon: '/favicon.ico',
    badge: '/favicon.ico',
    tag: 'neochat-notification',
    requireInteraction: false
  };

  event.waitUntil(
    self.registration.showNotification('NeoChat', options)
  );
});

// Обработка клика на уведомление
self.addEventListener('notificationclick', (event) => {
  console.log('[SW] Notification clicked:', event);
  event.notification.close();
  
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then((clientList) => {
      // Если окно уже открыто, фокусируемся на нём
      for (let i = 0; i < clientList.length; i++) {
        if (clientList[i].url === '/' && 'focus' in clientList[i]) {
          return clientList[i].focus();
        }
      }
      // Если нет, открываем новое
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});

// Обработка сообщений от клиента
self.addEventListener('message', (event) => {
  console.log('[SW] Message received:', event.data);
  
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

console.log('[SW] Service Worker loaded successfully');
