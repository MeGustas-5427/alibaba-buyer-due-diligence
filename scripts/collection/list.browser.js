(() => {

  function bjDateToUnixSeconds(dateText) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText);
    if (!match) throw new Error(`Invalid date: ${dateText}`);
    const [, year, month, day] = match.map(Number);
    if (new Date(Date.UTC(year, month - 1, day)).toISOString().slice(0, 10) !== dateText)
      throw new Error('Invalid calendar date.');
    return String(Math.floor(Date.UTC(year, month - 1, day) / 1000) - 8 * 60 * 60);
  }

  function tokenFromCookie() {
    return document.cookie
      .split('; ')
      .find((item) => item.startsWith('_tb_token_='))
      ?.split('=')[1];
  }

  function buildFilters({ startDate, endDate, salesName, salesId }) {
    const filters = [
      {
        id: '667',
        gmt_create_start: bjDateToUnixSeconds(startDate),
        gmt_create_end: bjDateToUnixSeconds(endDate)
      }
    ];
    const resolvedSalesId = salesId ?? null;
    if (salesName && salesName !== '全部' && !resolvedSalesId) {
      throw new Error(`Unknown salesName: ${salesName}`);
    }
    if (resolvedSalesId !== null) filters.push({ id: '669', sales_id: resolvedSalesId });
    return { filters, salesId: resolvedSalesId };
  }

  async function queryCustomerList({ filters, pageNum, pageSize }) {
    const token = tokenFromCookie();
    if (!token) throw new Error('Missing _tb_token_; run this on alicrm.alibaba.com after login.');
    const requestBody = {
      jsonArray: JSON.stringify(filters),
      orderDescs: [{ col: 'opp_gmt_modified', asc: false }],
      pageNum,
      pageSize
    };
    const response = await fetch(
      `https://alicrm.alibaba.com/eggCrmQn/crm/customerQueryServiceI/queryCustomerList.json?_tb_token_=${encodeURIComponent(token)}`,
      {
        method: 'POST',
        signal: AbortSignal.timeout(30000),
        credentials: 'include',
        headers: { 'content-type': 'application/json;charset=UTF-8' },
        body: JSON.stringify(requestBody)
      }
    );
    if (response.redirected || [401, 403, 429].includes(response.status)) {
      throw new Error(`CRM authorization/risk gate: HTTP ${response.status}`);
    }
    const json = await response.json();
    if (!response.ok || !json.success) {
      const message = String(json.message || '');
      const pageSizeRejected = (response.status === 413 || /page.?size|每页|单页|条数|请求.*过大/i.test(message)) &&
        !/login|captcha|token|auth|session|risk|登录|验证|风控|过期/i.test(message);
      const error = new Error(pageSizeRejected ? 'CRM page size rejected' : `CRM API probe failed: HTTP ${response.status}`);
      error.pageSizeRejected = pageSizeRejected;
      throw error;
    }
    return { status: response.status, ok: response.ok, requestBody, json };
  }

  function checkPage(data, previous) {
    if (!Number.isSafeInteger(data.total) || data.total < 0 || !Array.isArray(data.data) ||
      (previous !== null && previous !== data.total)) throw new Error('Invalid or changing CRM total.');
  }

  function fallbackPageNums(pageNum, pageSize, fallbackPageSize, total) {
    const startOffset = (pageNum - 1) * pageSize;
    const endOffset = Math.min(pageNum * pageSize, total);
    const first = Math.floor(startOffset / fallbackPageSize) + 1;
    const last = Math.ceil(endOffset / fallbackPageSize);
    return Array.from({ length: Math.max(0, last - first + 1) }, (_, index) => first + index);
  }

  async function collectAliCrmCustomerList(options) {
    selfCheck();
    if (!(location.hostname === 'alicrm.alibaba.com' ||
      (location.hostname === 'i.alibaba.com' && location.pathname.startsWith('/hub/alicrm/')))) {
      throw new Error('Run this script on alicrm.alibaba.com.');
    }

    const pageSize = 500;
    const fallbackPageSize = 100;
    const { filters, salesId } = buildFilters(options);
    const records = [];
    const pageRequests = [];
    let total = null;
    let crmSourceRoleId = null;
    let pageCount = 1;

    async function collectAllWithFallback(firstError) {
      let fallbackPageCount = 1;
      for (let fallbackPageNum = 1; fallbackPageNum <= fallbackPageCount; fallbackPageNum += 1) {
        const fallbackResult = await queryCustomerList({
          filters,
          pageNum: fallbackPageNum,
          pageSize: fallbackPageSize
        });
        const fallbackData = fallbackResult.json.data || {};
        checkPage(fallbackData, total); total = fallbackData.total;
        crmSourceRoleId = fallbackData.crmSourceRoleId || crmSourceRoleId;
        if (total > 5000) {
          const error = new Error('CRM total exceeds 5000; verified account sales segments required.');
          error.total = total;
          throw error;
        }
        fallbackPageCount = Math.max(1, Math.ceil(total / fallbackPageSize));
        checkPage(fallbackData, total);
        const fallbackRows = fallbackData.data;
        records.push(...fallbackRows);
        pageRequests.push({
          pageNum: null,
          pageSize,
          fallbackPageNum,
          fallbackPageSize,
          requestBody: fallbackResult.requestBody,
          count: fallbackRows.length,
          recoveredFrom: firstError.message || String(firstError)
        });
      }
      pageCount = Math.max(1, Math.ceil(total / pageSize));
    }

    for (let pageNum = 1; pageNum <= pageCount; pageNum += 1) {
      let result;
      try {
        result = await queryCustomerList({ filters, pageNum, pageSize });
      } catch (error) {
        if (!error.pageSizeRejected) throw error;
        if (pageNum === 1 && fallbackPageSize < pageSize) {
          await collectAllWithFallback(error);
          break;
        }
        if (!total || fallbackPageSize >= pageSize) throw error;
        const fallbackPages = fallbackPageNums(pageNum, pageSize, fallbackPageSize, total);
        let fallbackCount = 0;
        for (const fallbackPageNum of fallbackPages) {
          const fallbackResult = await queryCustomerList({
            filters,
            pageNum: fallbackPageNum,
            pageSize: fallbackPageSize
          });
          const fallbackData = fallbackResult.json.data || {};
          const fallbackRows = fallbackData.data || [];
          records.push(...fallbackRows);
          fallbackCount += fallbackRows.length;
          pageRequests.push({
            pageNum,
            pageSize,
            fallbackPageNum,
            fallbackPageSize,
            requestBody: fallbackResult.requestBody,
            count: fallbackRows.length,
            recoveredFrom: error.message || String(error)
          });
        }
        pageRequests.push({ pageNum, pageSize, count: fallbackCount, recovered: true });
        continue;
      }
      const data = result.json.data || {};
      checkPage(data, total); total = data.total;
      crmSourceRoleId = data.crmSourceRoleId || crmSourceRoleId;
      if (total > 5000) {
        const error = new Error('CRM total exceeds 5000; verified account sales segments required.');
          error.total = total;
          throw error;
      }
      pageCount = Math.max(1, Math.ceil(total / pageSize));
      records.push(...(data.data || []));
      pageRequests.push({
        pageNum,
        pageSize,
        requestBody: result.requestBody,
        count: (data.data || []).length
      });
    }

    if (records.length !== total || new Set(records.map(r => String(r.customerId || ''))).size !== total || records.some(r => !r.customerId)) throw new Error('Incomplete or duplicate CRM list.');
    return {
      status: 200,
      ok: true,
      capturedAt: new Date().toISOString(),
      collector: 'collect_customer_list_filtered.browser.js',
      criteria: {
        startDate: options.startDate,
        endDate: options.endDate,
        salesName: options.salesName || '全部',
        salesId,
        pageSize,
        total,
        pageCount,
        recordsCollected: records.length
      },
      requestBody: pageRequests[0]?.requestBody || null,
      pageRequests,
      json: {
        success: true,
        data: {
          total,
          data: records,
          crmSourceRoleId
        },
        message: '',
        code: ''
      }
    };
  }

  function selfCheck() {
    console.assert(bjDateToUnixSeconds('2018-05-09') === '1525795200');
    console.assert(bjDateToUnixSeconds('2018-05-10') === '1525881600');
    const built = buildFilters({
      startDate: '2018-05-09',
      endDate: '2018-05-10',
      salesId: 'synthetic-sales-id'
    });
    console.assert(built.filters[0].id === '667');
    console.assert(built.filters[1].sales_id === 'synthetic-sales-id');
    console.assert(fallbackPageNums(2, 500, 100, 2636).join(',') === '6,7,8,9,10');
    console.assert(fallbackPageNums(6, 500, 100, 2636).join(',') === '26,27');
  }

  async function collectDaily(options) {
    try { return await collectAliCrmCustomerList(options); }
    catch (error) {
      if (!error.total || !options.salesSegments) throw error;
      const segments = options.salesSegments;
      if (!Array.isArray(segments) || !segments.length || segments.some(s => typeof s.salesId !== 'string'))
        throw new Error('Verified sales mapping including unassigned is required.');
      const results = [];
      const merged = new Map();
      for (const segment of segments) {
        const part = await collectAliCrmCustomerList({...options, salesId: segment.salesId, salesName: segment.name || 'segment'});
        results.push(part);
        for (const row of part.json.data.data) {
          const id = String(row.customerId);
          if (merged.has(id) && JSON.stringify(merged.get(id)) !== JSON.stringify(row))
            throw new Error('Overlapping segments have inconsistent customer data.');
          merged.set(id, row);
        }
      }
      if (merged.size !== error.total) throw new Error('Segment coverage differs from global total.');
      const {filters} = buildFilters(options);
      const check = await queryCustomerList({filters, pageNum: 1, pageSize: 100});
      if (check.json.data.total !== error.total) throw new Error('CRM total changed during split collection.');
      return {status: 200, ok: true, capturedAt: new Date().toISOString(),
        criteria: {startDate: options.startDate, endDate: options.endDate, salesName: '全部', salesId: null,
          pageSize: 500, total: error.total, recordsCollected: merged.size},
        requestBody: check.requestBody, segments: results,
        json: {success: true, data: {total: error.total, data: [...merged.values()]}}};
    }
  }

  selfCheck();
  const api = {
    bjDateToUnixSeconds,
    buildFilters,
    fallbackPageNums,
    collectAliCrmCustomerList, collectDaily
  };

  if (typeof window !== 'undefined') {
    window.collectAliCrmCustomerList = collectDaily;
    window.aliCrmCustomerCollector = api;
    return 'collectAliCrmCustomerList ready';
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  return api;
})();
