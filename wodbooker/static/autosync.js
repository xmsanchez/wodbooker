// Auto-sync WodBuster bookings on page load
(function() {
    'use strict';
    
    let isSyncing = false;
    
    // Check if we're on the booking list page
    function isBookingListPage() {
        return window.location.pathname.includes('/booking/') || 
               window.location.pathname === '/' ||
               window.location.pathname === '/booking';
    }
    
    // Perform sync
    async function performSync() {
        if (isSyncing) {
            return;
        }
        
        isSyncing = true;
        
        // Show loading indicator
        const loadingIndicator = document.getElementById('autosync-loading');
        if (loadingIndicator) {
            loadingIndicator.style.display = 'block';
        }
        
        try {
            // Get CSRF token
            const csrfToken = window.CSRF_TOKEN || getCSRFToken();
            
            const headers = {
                'Content-Type': 'application/json',
            };
            
            // Add CSRF token if available (Flask-WTF accepts it in header)
            if (csrfToken) {
                headers['X-CSRFToken'] = csrfToken;
            }
            
            // Send token in body as well for compatibility
            const body = {};
            if (csrfToken) {
                body.csrf_token = csrfToken;
            }
            
            const response = await fetch('/api/wodbuster/sync', {
                method: 'POST',
                headers: headers,
                body: Object.keys(body).length > 0 ? JSON.stringify(body) : undefined
            });
            
            const data = await response.json();
            
            // Hide loading indicator
            if (loadingIndicator) {
                loadingIndicator.style.display = 'none';
            }
            
            // Show result message
            if (data.success) {
                showSyncMessage(data.message, 'success');
                refreshAttendanceDashboard();
            } else {
                showSyncMessage('Error: ' + (data.error || 'Error desconocido'), 'error');
            }
        } catch (error) {
            console.error('Error syncing WodBuster bookings:', error);
            
            // Hide loading indicator
            if (loadingIndicator) {
                loadingIndicator.style.display = 'none';
            }
            
            showSyncMessage('Error al sincronizar: ' + error.message, 'error');
        } finally {
            isSyncing = false;
        }
    }
    
    // Show sync message
    function showSyncMessage(message, type) {
        if (typeof window.showSyncPopup === 'function') {
            window.showSyncPopup(message, type);
            return;
        }
        // Non-blocking fallback for pages without popup helper.
        console.warn('Sync message:', message);
    }
    
    async function refreshAttendanceDashboard() {
        const card = document.getElementById('attendanceSummaryCard');
        if (!card) return;
        try {
            const res = await fetch('/api/attendance/dashboard');
            const data = await res.json();
            if (!data.available) return;
            const label = document.getElementById('attendanceQuotaLabel');
            if (label && data.quota_mood) label.textContent = data.quota_mood.label;
            const cal = document.getElementById('attendanceCalendarSummary');
            if (cal && data.calendar_month) cal.textContent = data.calendar_month.summary_line;
            const credits = document.getElementById('attendanceCreditsLine');
            if (credits && data.billing_period) credits.textContent = data.billing_period.credits_line;
            const borrada = document.getElementById('attendanceBorradaLine');
            if (borrada) {
                if (data.billing_period && data.billing_period.cancelled_line) {
                    borrada.textContent = data.billing_period.cancelled_line;
                    borrada.style.display = '';
                } else {
                    borrada.style.display = 'none';
                }
            }
            const yoyMsg = document.getElementById('attendanceYoyMessage');
            if (yoyMsg && data.yoy_mood) yoyMsg.textContent = data.yoy_mood.message || '';
            const yoyDet = document.getElementById('attendanceYoyDetail');
            if (yoyDet && data.yoy_mood) yoyDet.textContent = data.yoy_mood.detail || '';
        } catch (e) {
            console.warn('Attendance dashboard refresh failed', e);
        }
    }

    // Get CSRF token from meta tag or hidden input
    function getCSRFToken() {
        // Try to get from meta tag
        const metaTag = document.querySelector('meta[name="csrf-token"]');
        if (metaTag) {
            return metaTag.getAttribute('content');
        }
        
        // Try to get from hidden input
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        if (csrfInput) {
            return csrfInput.value;
        }
        
        return null;
    }
    
    // Check if auto-sync is enabled and perform sync
    async function checkAndSync() {
        // Only sync on booking list page
        if (!isBookingListPage()) {
            return;
        }
        
        // Check if user has auto-sync enabled
        // We'll need to get this from the page or make an API call
        // For now, we'll check if the setting exists in the page context
        // This will be set by the template
        
        const autosyncEnabled = window.AUTOSYNC_ENABLED;
        if (autosyncEnabled) {
            // Wait a bit for page to fully load
            setTimeout(() => {
                performSync();
            }, 1000);
        }
    }
    
    // Initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', checkAndSync);
    } else {
        checkAndSync();
    }
    
    // Export function for manual sync
    window.AutoSync = {
        sync: performSync
    };
})();

