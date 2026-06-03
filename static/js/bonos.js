async function fetchBonos() {
    const tableBody = document.getElementById('bonos-table-body');
    const skeleton = document.getElementById('table-skeleton');
    
    try {
        const response = await fetch('/api/bonos-activos');
        const data = await response.json();
        
        skeleton.style.display = 'none';
        tableBody.innerHTML = '';
        
        data.forEach(bono => {
            const tr = document.createElement('tr');
            
            const progress = bono.min_estimados > 0 
                ? Math.min((bono.min_reales / bono.min_estimados) * 100, 100) 
                : 0;
            const progressColor = progress > 100 ? '#ef4444' : '#3b82f6';
            
            const semClass = (bono.semaforo || 'SIN ESTIMAR').toLowerCase().replace(/ /g, '-');
            const semText = bono.semaforo || 'SIN ESTIMAR';
            
            const fechaInicio = bono.fecha_inicio_real 
                ? new Date(bono.fecha_inicio_real).toLocaleString('es-ES', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
                : '-';

            tr.innerHTML = `
                <td>
                    <span class="order-id">${bono.idorden}</span>
                    <span class="art-desc">${bono.idarticulo}</span>
                </td>
                <td>
                    <span class="fw-bold text-azure">${bono.idbono}</span>
                    <div class="small text-muted">${bono.operacion}</div>
                </td>
                <td>
                    <div class="d-flex align-items-center gap-2">
                        <div class="progress-premium">
                            <div class="progress-bar-premium" style="width: ${progress}%; background: ${progressColor}"></div>
                        </div>
                        <span class="small fw-600">${Math.round(progress)}%</span>
                    </div>
                    <div class="small text-muted mt-1">
                        ${bono.min_reales.toFixed(0)} / ${bono.min_estimados ? bono.min_estimados.toFixed(0) : '?'} min
                    </div>
                </td>
                <td>
                    <span class="sem-badge sem-${semClass}">
                        <span class="sem-dot"></span>
                        ${semText}
                    </span>
                </td>
                <td>
                    <div class="small fw-medium">${fechaInicio}</div>
                    <div class="small text-muted">${bono.num_operarios} operario(s)</div>
                </td>
                <td>
                    <span class="badge ${bono.en_curso ? 'bg-success-lt' : 'bg-secondary-lt'}">
                        ${bono.situacion || (bono.en_curso ? 'EN CURSO' : 'PENDIENTE')}
                    </span>
                </td>
            `;
            tableBody.appendChild(tr);
        });
        
    } catch (error) {
        console.error('Error fetching bonos:', error);
        skeleton.innerHTML = '<td colspan="6" class="text-center text-danger padding-4">Error al cargar datos. Compruebe la conexión con la base de datos.</td>';
    }
}

document.addEventListener('DOMContentLoaded', fetchBonos);
// Refresh auto every 30 seconds
setInterval(fetchBonos, 30000);
