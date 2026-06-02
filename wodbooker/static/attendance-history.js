(function () {
    'use strict';
    const data = window.ATTENDANCE_HISTORY_DATA;
    if (!data || !Array.isArray(data.months)) return;
    const canvas = document.getElementById('attendanceChart');
    if (!canvas || typeof Chart === 'undefined') return;

    const rangeBtn = document.getElementById('historyRangeToggleBtn');
    const granularityBtn = document.getElementById('historyGranularityToggleBtn');
    const allMonths = data.months.slice();

    let showFullHistory = false;
    let showYearly = false;
    let currentSeries = null;

    function aggregateYearly(monthRows) {
        const byYear = new Map();
        monthRows.forEach((row) => {
            const key = String(row.year);
            if (!byYear.has(key)) {
                byYear.set(key, {
                    label: key,
                    year: row.year,
                    attended: 0,
                    booked: 0,
                    noShow: 0,
                    cancelled: 0,
                    hasCancelledData: false,
                });
            }
            const agg = byYear.get(key);
            agg.attended += row.attended || 0;
            agg.booked += row.booked || 0;
            agg.noShow += row.no_show || 0;
            if (row.cancelled !== null && row.cancelled !== undefined) {
                agg.cancelled += row.cancelled || 0;
                agg.hasCancelledData = true;
            }
        });

        const yearlyRows = Array.from(byYear.values()).sort((a, b) => a.year - b.year);
        const attendedByYear = new Map(yearlyRows.map((row) => [row.year, row.attended]));
        const prevYear = yearlyRows.map((row) => attendedByYear.get(row.year - 1) || 0);
        return {
            labels: yearlyRows.map((row) => row.label),
            attended: yearlyRows.map((row) => row.attended),
            booked: yearlyRows.map((row) => row.booked),
            noShow: yearlyRows.map((row) => row.noShow),
            cancelled: yearlyRows.map((row) => (row.hasCancelledData ? row.cancelled : null)),
            prevYearAttended: prevYear,
        };
    }

    function monthlySeries(monthRows) {
        return {
            labels: monthRows.map((row) => row.label),
            attended: monthRows.map((row) => row.attended || 0),
            booked: monthRows.map((row) => row.booked || 0),
            noShow: monthRows.map((row) => row.no_show || 0),
            cancelled: monthRows.map((row) => (
                row.cancelled === null || row.cancelled === undefined ? null : row.cancelled
            )),
            prevYearAttended: monthRows.map((row) => row.prev_year_attended || 0),
        };
    }

    function applyRangeLimit(series) {
        if (showFullHistory || series.labels.length <= 13) {
            return series;
        }
        return {
            labels: series.labels.slice(-13),
            attended: series.attended.slice(-13),
            booked: series.booked.slice(-13),
            noShow: series.noShow.slice(-13),
            cancelled: series.cancelled.slice(-13),
            prevYearAttended: series.prevYearAttended.slice(-13),
        };
    }

    function buildSeries() {
        const base = showYearly ? aggregateYearly(allMonths) : monthlySeries(allMonths);
        return applyRangeLimit(base);
    }

    function syncButtonLabels() {
        if (rangeBtn) {
            rangeBtn.textContent = showFullHistory
                ? 'Mostrar último año'
                : 'Mostrar todos los años';
        }
        if (granularityBtn) {
            granularityBtn.textContent = showYearly
                ? 'Mostrar mensualmente'
                : 'Mostrar anualmente';
        }
    }

    currentSeries = buildSeries();
    const chart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels: currentSeries.labels,
            datasets: [
                {
                    label: 'Asistidas',
                    data: currentSeries.attended,
                    backgroundColor: 'rgba(37, 99, 235, 0.75)',
                },
                {
                    label: 'Reservadas',
                    data: currentSeries.booked,
                    backgroundColor: 'rgba(100, 116, 139, 0.5)',
                },
                {
                    label: 'Asistidas año anterior',
                    data: currentSeries.prevYearAttended,
                    type: 'line',
                    borderColor: 'rgba(234, 179, 8, 1)',
                    backgroundColor: 'transparent',
                    tension: 0.2,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    filter: function (ctx) {
                        return ctx.datasetIndex === 0;
                    },
                    callbacks: {
                        title: function (items) {
                            return items.length ? items[0].label : '';
                        },
                        label: function (ctx) {
                            const idx = ctx.dataIndex;
                            return [
                                'Asistidas: ' + (currentSeries.attended[idx] || 0),
                                'Reservadas: ' + (currentSeries.booked[idx] || 0),
                                'No presentado: ' + (currentSeries.noShow[idx] || 0),
                                'Canceladas: ' + (
                                    currentSeries.cancelled[idx] === null
                                        ? '—'
                                        : currentSeries.cancelled[idx]
                                ),
                            ];
                        },
                    },
                },
            },
            scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } },
        },
    });

    function rerenderChart() {
        currentSeries = buildSeries();
        chart.data.labels = currentSeries.labels;
        chart.data.datasets[0].data = currentSeries.attended;
        chart.data.datasets[1].data = currentSeries.booked;
        chart.data.datasets[2].data = currentSeries.prevYearAttended;
        chart.update();
        syncButtonLabels();
    }

    if (rangeBtn) {
        rangeBtn.addEventListener('click', function () {
            showFullHistory = !showFullHistory;
            rerenderChart();
        });
    }
    if (granularityBtn) {
        granularityBtn.addEventListener('click', function () {
            showYearly = !showYearly;
            rerenderChart();
        });
    }
    syncButtonLabels();
})();
