(function () {
    'use strict';

    function csrfHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        const token = window.CSRF_TOKEN || document.querySelector('meta[name="csrf-token"]')?.content;
        if (token) headers['X-CSRFToken'] = token;
        return headers;
    }

    function setSpinner(btn, active) {
        if (!btn) return;
        if (active) {
            btn.setAttribute('data-original-html', btn.innerHTML);
            btn.innerHTML = '<i class="bi bi-arrow-clockwise" style="animation: spin 1s linear infinite; display: inline-block;"></i> ' + btn.textContent.trim();
        } else {
            var orig = btn.getAttribute('data-original-html');
            if (orig) btn.innerHTML = orig;
        }
    }

    function nextPaint() {
        return new Promise((resolve) => {
            window.requestAnimationFrame(() => {
                setTimeout(resolve, 0);
            });
        });
    }

    async function runBackfill(statusEl, btn) {
        if (btn) btn.disabled = true;
        setSpinner(btn, true);
        if (statusEl) statusEl.textContent = 'Sincronizando historial…';
        await nextPaint();
        try {
            const res = await fetch('/api/attendance/backfill', {
                method: 'POST',
                headers: csrfHeaders(),
            });
            const data = await res.json();
            if (!data.success) {
                if (statusEl) statusEl.textContent = 'Error: ' + (data.error || 'desconocido');
                return;
            }
            const msg = data.complete
                ? 'Historial completo.'
                : `Añadidos ${data.months_filled} meses. Quedan ${data.months_remaining}.`;
            if (statusEl) statusEl.textContent = msg;
            if (data.complete) {
                setTimeout(() => window.location.reload(), 800);
            }
        } catch (e) {
            if (statusEl) statusEl.textContent = 'Error: ' + e.message;
        } finally {
            setSpinner(btn, false);
            if (btn) btn.disabled = false;
        }
    }

    const backfillBtn = document.getElementById('attendanceBackfillBtn');
    const statusEl = document.getElementById('attendanceBackfillStatus');
    if (backfillBtn) {
        backfillBtn.addEventListener('click', () => runBackfill(statusEl, backfillBtn));
    }

    const regenBtn = document.getElementById('regenerateHistoryBtn');
    if (regenBtn) {
        regenBtn.addEventListener('click', async () => {
            if (!confirm(
                '¿Borrar el historial almacenado y volver a sincronizar desde WodBuster? '
                + 'No afecta tus reservas en WodBuster.'
            )) {
                return;
            }
            regenBtn.disabled = true;
            setSpinner(regenBtn, true);
            if (statusEl) statusEl.textContent = 'Regenerando historial…';
            await nextPaint();
            try {
                await fetch('/api/attendance/regenerate-history', {
                    method: 'POST',
                    headers: csrfHeaders(),
                });
                setSpinner(regenBtn, false);
                await runBackfill(statusEl, null);
            } catch (e) {
                alert('Error: ' + e.message);
            } finally {
                setSpinner(regenBtn, false);
                regenBtn.disabled = false;
            }
        });
    }
})();
