// Executed only inside the existing authorized Alibaba page, never in read-only DOM evaluation.
const request = async (path, body) => {
  const response = await fetch(settings.origin+path, {
    method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer '+settings.credential},
    body: JSON.stringify({runId: settings.runId, kind: settings.kind, ...body})
  });
  if (!response.ok) throw new Error('Local collection bridge rejected the request.');
  return response.json();
};
if (window.__alibabaLocalCollection?.running) throw new Error('A collector is already running on this page.');
window.__alibabaLocalCollection = {running: true, runId: settings.runId, kind: settings.kind};
window.__alibabaLocalCollectionPromise = (async () => {
  try {
    if (settings.kind === 'list') {
      const value = await window.collectAliCrmCustomerList(settings.options);
      await request('/results', {value});
    } else {
      for (;;) {
        const batch = await request('/next', {});
        if (!batch.rows.length) break;
        const collect = settings.kind === 'detail' ? window.collectAliCrmCustomerDetailMissingFields : window.collectAliBuyerProfileFields;
        const result = await collect(batch.rows, {concurrency: 1, stopOnError: true});
        await request('/results', {lease: batch.lease, results: result.results});
        if (result.results.some(row => row.error)) throw new Error('Collection stopped after a failed row.');
      }
    }
    window.__alibabaLocalCollection.status = 'saved_requires_python_validation';
  } catch {
    window.__alibabaLocalCollection.status = 'failed_review_login_risk_or_page_format';
    try { await request('/failure', {}); } catch {}
  } finally {
    window.__alibabaLocalCollection.running = false;
  }
})();
return {started: true, kind: settings.kind};
