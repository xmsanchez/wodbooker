// Push Notification Management
(function() {
    'use strict';
    
    let vapidPublicKey = null;
    let registration = null;
    
    // Get VAPID public key from server
    async function getVAPIDPublicKey() {
        if (vapidPublicKey) {
            return vapidPublicKey;
        }
        
        try {
            const response = await fetch('/api/push/vapid-public-key');
            const data = await response.json();
            if (response.ok && data.publicKey) {
                vapidPublicKey = data.publicKey;
                return vapidPublicKey;
            } else {
                // Server returned an error
                const errorMsg = data.message || data.error || 'Error desconocido';
                console.error('Error getting VAPID public key:', errorMsg);
                throw new Error(errorMsg);
            }
        } catch (error) {
            console.error('Error getting VAPID public key:', error);
            if (error.message) {
                throw error; // Re-throw if it's already an Error object with a message
            }
            throw new Error('No se pudo obtener la clave pública VAPID del servidor. Por favor, contacta al administrador.');
        }
    }
    
    // Convert VAPID key to Uint8Array
    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/\-/g, '+')
            .replace(/_/g, '/');
        
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        
        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }
    
    // Register service worker
    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            console.warn('Service Workers are not supported in this browser');
            return null;
        }
        
        try {
            registration = await navigator.serviceWorker.register('/static/sw.js');
            console.log('Service Worker registered successfully:', registration.scope);
            return registration;
        } catch (error) {
            console.error('Service Worker registration failed:', error);
            console.error('Error details:', {
                message: error.message,
                name: error.name,
                stack: error.stack
            });
            return null;
        }
    }
    
    // Request notification permission
    async function requestNotificationPermission() {
        if (!('Notification' in window)) {
            console.log('This browser does not support notifications');
            return false;
        }
        
        if (Notification.permission === 'granted') {
            return true;
        }
        
        if (Notification.permission !== 'denied') {
            const permission = await Notification.requestPermission();
            return permission === 'granted';
        }
        
        return false;
    }
    
    // Subscribe to push notifications
    async function subscribeToPush() {
        console.log('=== Starting push subscription process ===');
        
        if (!registration) {
            console.log('Service worker not registered, registering now...');
            registration = await registerServiceWorker();
            if (!registration) {
                throw new Error('No se pudo registrar el service worker. Asegúrate de que tu navegador soporte service workers.');
            }
            console.log('Service worker registered successfully');
        } else {
            console.log('Service worker already registered');
        }
        
        console.log('Requesting notification permission...');
        const permission = await requestNotificationPermission();
        if (!permission) {
            throw new Error('Permiso de notificaciones denegado. Por favor, permite las notificaciones en la configuración de tu navegador.');
        }
        console.log('Notification permission granted');
        
        console.log('Fetching VAPID public key...');
        const vapidKey = await getVAPIDPublicKey();
        if (!vapidKey) {
            throw new Error('No se pudo obtener la clave pública VAPID del servidor. Por favor, contacta al administrador.');
        }
        console.log('VAPID public key obtained');
        
        try {
            console.log('Subscribing to push manager...');
            const subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(vapidKey)
            });
            console.log('Push subscription created successfully, endpoint:', subscription.endpoint.substring(0, 50) + '...');
            
            // Extract keys
            const p256dhKey = subscription.getKey('p256dh');
            const authKey = subscription.getKey('auth');
            
            if (!p256dhKey || !authKey) {
                throw new Error('No se pudieron obtener las claves de la suscripción push.');
            }
            
            console.log('Encoding subscription keys...');
            const p256dh = btoa(String.fromCharCode.apply(null, new Uint8Array(p256dhKey)));
            const auth = btoa(String.fromCharCode.apply(null, new Uint8Array(authKey)));
            
            console.log('Sending subscription to server...');
            // Send subscription to server
            const response = await fetch('/api/push/subscribe', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    endpoint: subscription.endpoint,
                    keys: {
                        p256dh: p256dh,
                        auth: auth
                    }
                })
            });
            
            console.log('Server response status:', response.status);
            
            if (response.ok) {
                const result = await response.json();
                console.log('Successfully subscribed to push notifications:', result);
                return subscription;
            } else {
                const errorData = await response.json().catch(() => ({ error: 'Unknown error' }));
                console.error('Failed to register subscription on server:', response.status, errorData);
                throw new Error(errorData.error || `Error del servidor: ${response.status}`);
            }
        } catch (error) {
            console.error('Error subscribing to push notifications:', error);
            console.error('Error details:', {
                name: error.name,
                message: error.message,
                stack: error.stack
            });
            // Re-throw the error so it can be caught and displayed to the user
            throw error;
        }
    }
    
    // Unsubscribe from push notifications
    async function unsubscribeFromPush() {
        if (!registration) {
            return false;
        }
        
        try {
            const subscription = await registration.pushManager.getSubscription();
            if (subscription) {
                await subscription.unsubscribe();
                
                // Notify server
                await fetch('/api/push/unsubscribe', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        endpoint: subscription.endpoint
                    })
                });
                
                console.log('Successfully unsubscribed from push notifications');
                return true;
            }
        } catch (error) {
            console.error('Error unsubscribing from push notifications:', error);
        }
        return false;
    }
    
    // Check if user is subscribed
    async function isSubscribed() {
        if (!isSupported()) {
            return false;
        }
        
        if (!registration) {
            registration = await registerServiceWorker();
            if (!registration) {
                console.warn('Service worker registration failed, cannot check subscription');
                return false;
            }
        }
        
        try {
            const subscription = await registration.pushManager.getSubscription();
            return subscription !== null;
        } catch (error) {
            console.error('Error checking subscription:', error);
            return false;
        }
    }
    
    // Initialize push notifications
    async function initializePushNotifications() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
            console.log('Push notifications not supported in this browser');
            return;
        }
        
        // Register service worker
        await registerServiceWorker();
        
        // If user has push notifications enabled, try to subscribe
        // This will be called from the user settings page
    }
    
    // Check if push notifications are supported
    function isSupported() {
        return 'serviceWorker' in navigator && 'PushManager' in window;
    }
    
    // Export functions to global scope
    window.PushNotifications = {
        isSupported: isSupported,
        subscribe: subscribeToPush,
        unsubscribe: unsubscribeFromPush,
        isSubscribed: isSubscribed,
        requestPermission: requestNotificationPermission,
        initialize: initializePushNotifications
    };
    
    // Auto-initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializePushNotifications);
    } else {
        initializePushNotifications();
    }
})();

