// Service Worker for Push Notifications
self.addEventListener('push', function(event) {
    let data = {};
    
    if (event.data) {
        try {
            data = JSON.parse(event.data.text());
        } catch (e) {
            data = { title: 'WodBooker', body: event.data.text() };
        }
    }
    
    const title = data.title || 'WodBooker';
    const body = data.body || 'Nueva notificación';
    const options = {
        body: body,
        icon: '/static/icon-192x192.png', // You may want to add an icon
        badge: '/static/badge-72x72.png', // You may want to add a badge
        vibrate: [200, 100, 200],
        data: data,
        tag: 'wodbooker-notification',
        requireInteraction: false
    };
    
    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    
    // Focus or open the app
    event.waitUntil(
        clients.matchAll({
            type: 'window',
            includeUncontrolled: true
        }).then(function(clientList) {
            // If there's already a window open, focus it
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url === '/' && 'focus' in client) {
                    return client.focus();
                }
            }
            // Otherwise, open a new window
            if (clients.openWindow) {
                return clients.openWindow('/');
            }
        })
    );
});

